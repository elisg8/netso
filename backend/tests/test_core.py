import pytest

from netso import topology as topo
from netso.config import settings
from netso.engine import Engine, load_state, plan_links
from netso.models import Lab
from netso.hostcmd import Runner


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "home", tmp_path / "home")
    monkeypatch.setattr(settings, "images", tmp_path / "images")
    monkeypatch.setattr(settings, "template_dirs", [])
    monkeypatch.setattr(settings, "dry_run", True)
    monkeypatch.setattr(settings, "auth", False)
    monkeypatch.setattr(settings, "token", None)
    yield


EXAMPLE = """
name: t1
nodes:
  - {name: r1, template: cisco-iosv}
  - {name: r2, template: cisco-c8000v}
  - {name: h1, template: linux-host}
  - {name: sw, template: bridge}
  - {name: h2, template: alpine}
links:
  - endpoints: [r1:Gi0/0, r2:Gi1]
  - endpoints: [r1:Gi0/1, h1:eth1]
  - endpoints: [h1:eth2, sw:port1]
  - endpoints: [h2:eth1, sw:port2]
"""


def test_templates_loaded():
    t = topo.load_templates()
    assert {"cisco-iosv", "linux-host", "bridge", "external"} <= set(t)
    assert t["cisco-iosv"].interface_names()[:2] == ["Gi0/0", "Gi0/1"]
    assert t["cisco-c8000v"].interface_names()[0] == "Gi1"
    assert t["linux-host"].interface_names()[0] == "eth1"


def test_validate_and_plan():
    lab = topo.parse_lab_yaml(EXAMPLE)
    resolved = topo.validate_lab(lab)
    owned, wiring = plan_links(lab, resolved)
    assert wiring["h1"]["eth2"] == wiring["h2"]["eth1"]  # same switch
    assert wiring["r1"]["Gi0/0"] == wiring["r2"]["Gi1"]
    assert len(owned) == 3  # 2 point-to-point + 1 switch
    assert all(len(b) <= 15 for b in owned)


@pytest.mark.parametrize("bad, msg", [
    ("links: [{endpoints: [r1:Gi9/9, h1:eth1]}]", "not available"),
    ("links: [{endpoints: [nope:eth1, h1:eth1]}]", "does not exist"),
    ("links: [{endpoints: [r1:Gi0/0, h1:eth1]}, {endpoints: [r1:Gi0/0, h1:eth2]}]", "used in two links"),
])
def test_validation_errors(bad, msg):
    text = "name: t2\nnodes:\n  - {name: r1, template: cisco-iosv}\n  - {name: h1, template: alpine}\n" + bad
    with pytest.raises(topo.TopologyError, match=msg):
        topo.validate_lab(topo.parse_lab_yaml(text))


def test_bad_name():
    with pytest.raises(topo.TopologyError):
        topo.parse_lab_yaml("name: '1 bad name'")


def test_deploy_destroy_dry_run():
    lab = topo.parse_lab_yaml(EXAMPLE)
    topo.save_lab(lab)
    r = Runner(force_dry_run=True)
    eng = Engine(r)
    st = eng.deploy("t1")
    assert st.status == "running", st.error
    assert all(n.status == "running" for n in st.nodes.values())
    assert st.nodes["r1"].console_port and st.nodes["r1"].vnc_display is not None
    assert st.nodes["r1"].console_port != st.nodes["r2"].console_port
    log = "\n".join(r.log)
    assert "qemu-system-x86_64" in log and "docker run" in log
    assert "nsenter" in log and "type bridge" in log
    # yaml round trip
    assert topo.parse_lab_yaml(topo.dump_lab_yaml(lab)) == lab
    st = eng.destroy("t1")
    assert st.status == "stopped" and not st.link_bridges
    assert load_state("t1").status == "stopped"


def test_link_conditioning_dry_run():
    text = """
name: netem1
nodes:
  - {name: h1, template: alpine}
  - {name: h2, template: alpine}
links:
  - endpoints: [h1:eth1, h2:eth1]
    delay_ms: 50
    jitter_ms: 10
    loss_pct: 2
    rate_kbit: 1000
"""
    lab = topo.parse_lab_yaml(text)
    # fields round-trip through YAML
    assert topo.parse_lab_yaml(topo.dump_lab_yaml(lab)) == lab
    topo.save_lab(lab)
    r = Runner(force_dry_run=True)
    eng = Engine(r)
    st = eng.deploy("netem1")
    assert st.status == "running", st.error
    netem = [l for l in r.log if "netem" in l and "add" in l]
    # applied on both endpoints (both docker veths)
    assert len(netem) == 2
    line = netem[0]
    assert "delay 50.0ms 10.0ms" in line and "loss 2.0%" in line and "rate 1000kbit" in line


def test_link_conditioning_validation():
    with pytest.raises(topo.TopologyError, match="between 0 and 100"):
        topo.parse_lab_yaml(
            "name: bad\nnodes: [{name: h1, template: alpine}, {name: h2, template: alpine}]\n"
            "links: [{endpoints: [h1:eth1, h2:eth1], loss_pct: 150}]"
        )


def test_api_dry_run():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    assert c.post("/api/labs", json={"yaml": EXAMPLE}).status_code == 201
    assert c.post("/api/labs", json={"yaml": EXAMPLE}).status_code == 409
    assert any(t["name"] == "frr" for t in c.get("/api/templates").json())
    labs = c.get("/api/labs").json()
    assert labs[0]["name"] == "t1" and labs[0]["nodes"] == 5
    assert "r1" in c.get("/api/labs/t1/yaml").text
    assert c.put("/api/labs/t1/yaml", json={"yaml": "name: t1\nnodes: [{name: x, template: nope}]"}).status_code == 400
    assert c.post("/api/labs/t1/deploy").status_code == 200
    assert c.delete("/api/labs/t1").status_code == 400
    assert c.post("/api/labs/t1/destroy").status_code == 200
    assert c.delete("/api/labs/t1").status_code == 200


def test_console_websocket(monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from netso import api as api_mod
    monkeypatch.setattr(api_mod.engine, "console_command", lambda lab, node: ["sh", "-c", "read x; echo got:$x"])
    c = TestClient(api_mod.app)
    with c.websocket_connect("/api/labs/x/nodes/y/console") as ws:
        ws.send_text(json.dumps({"t": "r", "c": 100, "r": 30}))
        ws.send_text(json.dumps({"t": "i", "d": "hello\n"}))
        out = ""
        for _ in range(10):
            out += ws.receive_text()
            if "got:hello" in out:
                break
    assert "got:hello" in out


def test_token_auth(monkeypatch):
    from fastapi.testclient import TestClient
    from netso.api import app
    monkeypatch.setattr(settings, "token", "s3cret")
    c = TestClient(app)
    assert c.get("/api/labs").status_code == 401
    assert c.get("/api/labs", headers={"Authorization": "Bearer s3cret"}).status_code == 200


STARTUP = """
name: cfg1
nodes:
  - {name: r1, template: cisco-iosv, startup_config: true}
  - {name: r2, template: cisco-c8000v, startup_config: true}
  - {name: f1, template: frr, startup_config: true}
  - {name: r3, template: cisco-iosv}
links: []
"""


def test_startup_config_dry_run():
    lab = topo.parse_lab_yaml(STARTUP)
    assert topo.parse_lab_yaml(topo.dump_lab_yaml(lab)) == lab
    assert "startup_config" not in topo.dump_lab_yaml(lab).split("r3")[1]  # OFF is not written
    topo.save_lab(lab)
    for n in ("r1", "r2", "f1", "r3"):
        topo.write_config("cfg1", "default", n, f"hostname {n}")
    r = Runner(force_dry_run=True)
    st = Engine(r).deploy("cfg1")
    assert st.status == "running", st.error
    log = "\n".join(r.log)
    # IOSv: FAT disk with ios_config.txt as an extra virtio disk
    assert "mkfs.vfat" in log and "::/ios_config.txt" in log
    assert "nodes/r1/config.img,if=virtio,index=1,format=raw" in log
    # C8000v: ISO with iosxe_config.txt as CD-ROM
    assert "genisoimage" in log and "-cdrom" in log and "r2/config.iso" in log
    # FRR: create, copy frr.conf, start
    assert "docker create" in log and "f1:/etc/frr/frr.conf" in log and "docker start netso-cfg1-f1" in log
    # startup-config OFF: no media for r3
    assert "nodes/r3/config" not in log


def test_startup_config_inactive_set_dry_run():
    topo.save_lab(topo.parse_lab_yaml(STARTUP))
    topo.create_config_set("cfg1", "lab2")
    topo.write_config("cfg1", "lab2", "r1", "hostname other")
    lab = topo.load_lab("cfg1")
    lab.config_set = "lab2"
    topo.save_lab(lab)
    r = Runner(force_dry_run=True)
    Engine(r).start_node("cfg1", "r2")  # nothing for r2 in the active set
    assert "genisoimage" not in "\n".join(r.log)
    Engine(r).start_node("cfg1", "r1")
    assert "mkfs.vfat" in "\n".join(r.log)


def test_config_sets_api():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    assert c.post("/api/labs", json={"yaml": STARTUP}).status_code == 201
    assert c.put("/api/labs/cfg1/configs/default/nodes/r1", json={"config": "hostname r1"}).status_code == 200
    assert c.post("/api/labs/cfg1/configs", json={"name": "ccna", "copy_from": "default"}).status_code == 201
    assert c.post("/api/labs/cfg1/configs", json={"name": "ccna"}).status_code == 400
    r = c.get("/api/labs/cfg1/configs").json()
    assert r["active"] == "default" and r["sets"]["ccna"]["r1"] == "hostname r1\n"
    assert c.post("/api/labs/cfg1/configs/ccna/activate").status_code == 200
    assert c.post("/api/labs/cfg1/configs/ccna/rename", json={"new": "ospf"}).status_code == 200
    assert c.get("/api/labs/cfg1/configs").json()["active"] == "ospf"
    exported = c.get("/api/labs/cfg1/configs/ospf/export").text
    assert c.post("/api/labs/cfg1/configs/import", json={"yaml": exported, "name": "copia"}).status_code == 201
    assert c.get("/api/labs/cfg1/configs").json()["sets"]["copia"]["r1"] == "hostname r1\n"
    assert c.delete("/api/labs/cfg1/configs/default").status_code == 400
    assert c.delete("/api/labs/cfg1/configs/ospf").status_code == 200
    assert c.get("/api/labs/cfg1/configs").json()["active"] == "default"
    assert c.post("/api/labs/cfg1/configs/enable", json={"nodes": {"r1": False, "r3": True}}).status_code == 200
    nodes = {n["name"]: n for n in c.get("/api/labs/cfg1").json()["lab"]["nodes"]}
    assert not nodes["r1"]["startup_config"] and nodes["r3"]["startup_config"]
    assert c.post("/api/labs/cfg1/configs/rename-node", json={"old": "r1", "new": "core"}).status_code == 200
    assert "core" in c.get("/api/labs/cfg1/configs").json()["sets"]["default"]


@pytest.mark.parametrize("line, proto, src, dst", [
    ("1.000001 52:54:00:00:00:01 > 52:54:00:00:00:02, ethertype IPv4 (0x0800), length 98: "
     "10.0.0.1 > 10.0.0.2: ICMP echo request, id 3, seq 1, length 64", "ICMP", "10.0.0.1", "10.0.0.2"),
    ("1.0 52:54:00:00:00:01 > ff:ff:ff:ff:ff:ff, ethertype ARP (0x0806), length 42: "
     "Request who-has 10.0.0.2 tell 10.0.0.1, length 28", "ARP", "52:54:00:00:00:01", "ff:ff:ff:ff:ff:ff"),
    ("1.0 52:54:00:00:00:01 > 52:54:00:00:00:02, ethertype IPv4 (0x0800), length 74: "
     "10.0.0.1.43210 > 10.0.0.2.22: Flags [S], seq 1, win 64240, length 0", "TCP", "10.0.0.1:43210", "10.0.0.2:22"),
    ("1.0 52:54:00:00:00:01 > 52:54:00:00:00:02, ethertype IPv4 (0x0800), length 140: "
     "10.0.0.1.179 > 10.0.0.2.51000: Flags [P.], seq 1:75, ack 1, win 501, length 74: BGP", "BGP", "10.0.0.1:179", "10.0.0.2:51000"),
    ("1.0 52:54:00:00:00:01 > 01:00:5e:00:00:05, ethertype IPv4 (0x0800), length 82: "
     "10.0.0.1 > 224.0.0.5: OSPFv2, Hello, length 48", "OSPF", "10.0.0.1", "224.0.0.5"),
    ("1.0 52:54:00:00:00:01 > 33:33:00:00:00:01, ethertype IPv6 (0x86dd), length 86: "
     "fe80::1 > ff02::1: ICMP6, neighbor advertisement, tgt is fe80::1, length 32", "ICMPv6", "fe80::1", "ff02::1"),
    ("1.0 52:54:00:00:00:01 > 52:54:00:00:00:02, ethertype IPv4 (0x0800), length 90: "
     "10.0.0.1.5000 > 10.0.0.2.53: 1234+ A? example.com. (29)", "DNS", "10.0.0.1:5000", "10.0.0.2:53"),
    ("1.0 52:54:00:00:00:01 > 01:80:c2:00:00:00, 802.3, length 60: LLC, dsap STP (0x42) Individual, "
     "ssap STP (0x42) Command, ctrl 0x03: STP 802.1d, Config, Flags [none]", "STP", "52:54:00:00:00:01", "01:80:c2:00:00:00"),
    ("1.0 52:54:00:00:00:01 > 52:54:00:00:00:02, ethertype 802.1Q (0x8100), length 102: vlan 10, p 0, "
     "ethertype IPv4 (0x0800), 10.0.0.1 > 10.0.0.2: ICMP echo reply, id 1, seq 1, length 64", "ICMP", "10.0.0.1", "10.0.0.2"),
])
def test_capture_parse(line, proto, src, dst):
    from netso.capture import parse_line
    p = parse_line(line)
    assert (p["proto"], p["src"], p["dst"]) == (proto, src, dst)
    assert p["len"] > 0 and p["info"]
    assert parse_line("tcpdump: listening on nst0") is None


def test_capture_dry_run():
    import time
    from netso.capture import CaptureError
    lab = topo.parse_lab_yaml(EXAMPLE)
    topo.save_lab(lab)
    r = Runner(force_dry_run=True)
    eng = Engine(r)
    with pytest.raises(Exception, match="is not running"):
        eng.capture_iface("t1", "r1", "Gi0/0")
    eng.deploy("t1")
    host = eng.capture_iface("t1", "r1", "Gi0/0")
    assert host.startswith("nst")
    assert eng.capture_iface("t1", "h1", "eth2").startswith("nsv")
    with pytest.raises(CaptureError):
        eng.captures.start("t1", "r1", "Gi0/0", host, "-w /etc/x")
    cap = eng.captures.start("t1", "r1", "Gi0/0", host, "icmp or arp")
    assert eng.captures.start("t1", "r1", "Gi0/0", host).id == cap.id  # one per endpoint
    assert f"tcpdump -i {host}" in r.log[-1] and "--print" in r.log[-1] and "'icmp or arp'" in r.log[-1]
    time.sleep(1)
    assert cap.count >= 2 and cap.since(0)[0]["n"] == 1
    assert eng.captures.file("t1", cap.id).read_bytes()[:4] == b"\xd4\xc3\xb2\xa1"
    eng.destroy("t1")  # stops captures
    assert not cap.running
    listed = eng.captures.list("t1")
    assert listed[0]["id"] == cap.id and listed[0]["node"] == "r1"
    eng.captures.delete("t1", cap.id)
    assert eng.captures.list("t1") == []


def test_capture_api(monkeypatch):
    from fastapi.testclient import TestClient
    from netso import api as api_mod
    c = TestClient(api_mod.app)
    c.post("/api/labs", json={"yaml": EXAMPLE})
    assert c.post("/api/labs/t1/captures", json={"node": "r1", "iface": "Gi0/0"}).status_code == 400
    c.post("/api/labs/t1/deploy")
    cap = c.post("/api/labs/t1/captures", json={"node": "r1", "iface": "Gi0/0"}).json()
    assert cap["running"]
    with c.websocket_connect(f"/api/labs/t1/captures/{cap['id']}/live") as ws:
        got = []
        for _ in range(10):
            got += ws.receive_json()["packets"]
            if got:
                break
    assert got and {"n", "t", "src", "dst", "proto", "info"} <= set(got[0])
    res = c.get("/api/labs/t1/capture-stream", params={"node": "r1", "iface": "Gi0/0"})
    assert res.status_code == 200 and res.content[:4] == b"\xd4\xc3\xb2\xa1"
    assert c.get(f"/api/labs/t1/captures/{cap['id']}/pcap").status_code == 200
    assert c.get("/api/labs/t1/captures/..%2Fx/pcap").status_code in (400, 404)
    assert c.post(f"/api/labs/t1/captures/{cap['id']}/stop").status_code == 200
    assert c.get("/api/labs/t1/captures").json()[0]["running"] is False
    c.post("/api/labs/t1/destroy")


def test_wireshark_dry_run(monkeypatch):
    import time
    from netso import wireshark
    monkeypatch.setattr(wireshark, "GRACE", 0.2)
    topo.save_lab(topo.parse_lab_yaml(EXAMPLE))
    r = Runner(force_dry_run=True)
    eng = Engine(r)
    eng.deploy("t1")
    host = eng.capture_iface("t1", "r1", "Gi0/0")
    s = eng.wireshark.start("t1", "r1", "Gi0/0", host, "tcp port 179")
    run = next(x for x in r.log if x.startswith("docker run") and "netso/wireshark" in x)
    assert "--network host" in run and f"IFACE={host}" in run and "'FILTER=tcp port 179'" in run
    assert f"OUTFILE=/captures/{s.id}.pcapng" in run and f"VNC_PORT={s.port}" in run
    s2 = eng.wireshark.start("t1", "r2", "Gi1", eng.capture_iface("t1", "r2", "Gi1"))
    assert s2.port != s.port
    assert {x["id"] for x in eng.wireshark.list("t1")} == {s.id, s2.id}
    eng.wireshark.attach(s)
    time.sleep(0.4)  # s2 had no viewer: stopped after the grace period, s is watched
    assert s2.stopped and not s.stopped
    eng.wireshark.detach(s)
    time.sleep(0.4)
    assert s.stopped and f"docker stop -t 3 {s.container}" in r.log
    s3 = eng.wireshark.start("t1", "r1", "Gi0/0", host)
    eng.destroy("t1")
    assert s3.stopped and eng.wireshark.list("t1") == []


def test_wireshark_vnc_proxy(monkeypatch):
    import socket
    import threading
    from fastapi.testclient import TestClient
    from netso import api as api_mod
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def echo():  # stands in for Xvnc
        conn, _ = srv.accept()
        conn.sendall(b"RFB 003.008\n")
        while data := conn.recv(1024):
            conn.sendall(data.upper())
        conn.close()

    threading.Thread(target=echo, daemon=True).start()
    c = TestClient(api_mod.app)
    c.post("/api/labs", json={"yaml": EXAMPLE})
    c.post("/api/labs/t1/deploy")
    info = c.post("/api/labs/t1/wireshark", json={"node": "r1", "iface": "Gi0/0"}).json()
    assert info["running"] and info["file"].endswith(".pcapng")
    s = api_mod.engine.wireshark.get("t1", info["id"])
    monkeypatch.setattr(s, "port", srv.getsockname()[1])
    with c.websocket_connect(f"/api/labs/t1/wireshark/{info['id']}/vnc", subprotocols=["binary"]) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        assert s.viewers == 1
        ws.send_bytes(b"hello")
        assert ws.receive_bytes() == b"HELLO"
    # the pcapng being recorded is listed as a running capture
    (topo.lab_dir("t1") / "captures" / info["file"]).write_bytes(b"\n\r\r\n")
    cap = next(x for x in c.get("/api/labs/t1/captures").json() if x["id"] == info["id"])
    assert cap["running"] and cap["wireshark"] and cap["format"] == "pcapng"
    assert c.get(f"/api/labs/t1/captures/{info['id']}/pcap").headers["content-type"] == "application/x-pcapng"
    assert c.delete(f"/api/labs/t1/wireshark/{info['id']}").status_code == 200
    view = c.post("/api/labs/t1/wireshark/open", json={"capture": info["id"]}).json()
    assert view["live"] is False and view["file"] == info["file"]
    assert f"READFILE=/captures/{info['file']}" in api_mod.engine.r.log[-1]
    assert c.post("/api/labs/t1/wireshark/open", json={"capture": "nope"}).status_code == 404
    c.delete(f"/api/labs/t1/wireshark/{view['id']}")
    assert c.get("/api/labs/t1/wireshark").json() == []
    c.post("/api/labs/t1/destroy")
    srv.close()


def _client():
    from fastapi.testclient import TestClient
    from netso.api import app
    return TestClient(app)


def test_package_ships_templates():
    from netso.config import PACKAGE_DIR
    assert (PACKAGE_DIR / "templates" / "cisco-iosv.yml").exists()


def test_login(monkeypatch):
    monkeypatch.setattr(settings, "auth", True)
    c = _client()
    assert c.get("/api/labs").status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "sbagliata"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "admin", "password": "Netso"}).json()
    assert r["default_password"] is True
    h = {"Authorization": f"Bearer {r['token']}"}
    assert c.get("/api/labs", headers=h).status_code == 200
    assert c.get("/api/auth/me", headers=h).json()["user"] == "admin"
    assert c.post("/api/auth/password", headers=h, json={"old": "x", "new": "nuova1"}).status_code == 400
    assert c.post("/api/auth/password", headers=h, json={"old": "Netso", "new": "nuova1"}).status_code == 200
    assert c.post("/api/auth/login", json={"username": "admin", "password": "Netso"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "nuova1"}).status_code == 200
    assert not (settings.home / "users.json").read_text().count("nuova1")
    c.post("/api/auth/logout", headers=h)
    assert c.get("/api/labs", headers=h).status_code == 401


def test_fixed_token_still_works(monkeypatch):
    monkeypatch.setattr(settings, "auth", True)
    monkeypatch.setattr(settings, "token", "segreto")
    c = _client()
    assert c.get("/api/labs", params={"token": "segreto"}).status_code == 200
    assert c.get("/api/labs", params={"token": "altro"}).status_code == 401


def test_folders():
    c = _client()
    assert c.post("/api/folders", json={"path": "CCNA/OSPF"}).status_code == 201
    assert c.get("/api/folders").json() == ["CCNA", "CCNA/OSPF"]
    assert c.post("/api/folders", json={"path": "../x"}).status_code == 400
    assert c.post("/api/labs", json={"name": "l1", "folder": "CCNA/OSPF"}).status_code == 201
    assert c.post("/api/labs", json={"name": "l2", "folder": "nope"}).status_code == 400
    assert c.post("/api/labs", json={"name": "l3"}).status_code == 201
    labs = {l["name"]: l["folder"] for l in c.get("/api/labs").json()}
    assert labs == {"l1": "CCNA/OSPF", "l3": ""}
    # rename moves sub-folders and labs along
    assert c.put("/api/folders", json={"path": "CCNA", "new": "Cisco"}).status_code == 200
    assert c.get("/api/folders").json() == ["Cisco", "Cisco/OSPF"]
    assert c.get("/api/labs").json()[0]["folder"] == "Cisco/OSPF"
    assert c.put("/api/folders", json={"path": "Cisco", "new": "Cisco/OSPF/x"}).status_code == 400
    # not empty
    assert c.delete("/api/folders", params={"path": "Cisco"}).status_code == 400
    assert c.post("/api/labs/l1/move", json={"folder": ""}).status_code == 200
    assert c.delete("/api/folders", params={"path": "Cisco"}).status_code == 200
    assert c.get("/api/folders").json() == []
    c.post("/api/folders", json={"path": "A"})
    c.post("/api/labs/l3/move", json={"folder": "A"})
    c.delete("/api/labs/l3")
    assert c.delete("/api/folders", params={"path": "A"}).status_code == 200


def test_users_and_meta(monkeypatch):
    monkeypatch.setattr(settings, "auth", True)
    c = _client()
    assert c.get("/api/auth/hint").json()["default_account"] is True
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "Netso"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.post("/api/users", headers=h, json={"username": "mario", "password": "pw1234"}).status_code == 201
    assert c.post("/api/users", headers=h, json={"username": "mario", "password": "pw1234"}).status_code == 409
    assert [u["name"] for u in c.get("/api/users", headers=h).json()] == ["admin", "mario"]
    assert c.delete("/api/users/admin", headers=h).status_code == 400  # yourself
    assert c.post("/api/auth/login", json={"username": "mario", "password": "pw1234"}).status_code == 200
    assert c.delete("/api/users/mario", headers=h).status_code == 200
    # lab author = creator, editable meta, modified time in the summary
    assert c.post("/api/labs", headers=h, json={"name": "m1"}).status_code == 201
    s = c.get("/api/labs", headers=h).json()[0]
    assert s["author"] == "admin" and s["modified"] and s["links"] == 0
    assert c.put("/api/labs/m1/meta", headers=h, json={"description": "d", "author": "io"}).status_code == 200
    s = c.get("/api/labs", headers=h).json()[0]
    assert (s["description"], s["author"]) == ("d", "io")


def test_link_style():
    lab = topo.parse_lab_yaml("""
name: s1
nodes: [{name: a, template: alpine}, {name: b, template: alpine}]
links:
  - {endpoints: [a:eth1, b:eth1], color: "#ff8800", style: dashed, width: 3, shape: curved, label: " uplink "}
""")
    l = lab.links[0]
    assert (l.color, l.style, l.width, l.shape, l.label) == ("#ff8800", "dashed", 3, "curved", "uplink")
    assert "color: '#ff8800'" in topo.dump_lab_yaml(lab)
    assert "shape" not in topo.dump_lab_yaml(topo.parse_lab_yaml("name: s2\nnodes: [{name: a, template: alpine}, {name: b, template: alpine}]\nlinks: [{endpoints: [a:eth1, b:eth1]}]"))
    for bad in ['color: red', 'width: 20', 'shape: wavy']:
        with pytest.raises(topo.TopologyError):
            topo.parse_lab_yaml("name: s3\nlinks: [{endpoints: [a:eth1, b:eth1], " + bad + "}]")


def test_node_overrides():
    from netso.network import nth_mac
    lab = topo.parse_lab_yaml("""
name: o1
nodes:
  - {name: r1, template: cisco-iosv, mac: "50-00-00-0F-00-FF", nic_model: e1000, console: vnc, icon: switch, size: 150, pinned: true}
  - {name: c1, template: alpine, console: vnc}
""")
    r = topo.validate_lab(lab)
    assert r["r1"].mac == "50:00:00:0f:00:ff" and r["r1"].nic_model == "e1000" and r["r1"].console == "vnc" and r["r1"].icon == "switch"
    assert r["c1"].console != "vnc"  # console override only for VMs
    assert nth_mac("50:00:00:0f:00:ff", 1) == "50:00:00:0f:01:00"
    assert "pinned: true" in topo.dump_lab_yaml(lab)
    for bad in ['mac: "01:00:00:00:00:00"', 'mac: zz', 'size: 5']:
        with pytest.raises(topo.TopologyError):
            topo.parse_lab_yaml("name: o2\nnodes: [{name: r1, template: alpine, " + bad + "}]")
    r = Runner(force_dry_run=True)
    topo.save_lab(lab)
    Engine(r).deploy("o1")
    cmd = "\n".join(r.log)
    assert "e1000,netdev=n0,mac=50:00:00:0f:00:ff" in cmd and "mac=50:00:00:0f:01:00" in cmd and "-vnc" in cmd


# ------------------------------------------------------------------ import / export / snapshots
import base64 as _b64
import io as _io
import json as _json
import zipfile as _zip

CLAB = """
name: srl-frr
topology:
  kinds:
    nokia_srlinux: {image: ghcr.io/nokia/srlinux}
  nodes:
    srl1: {kind: nokia_srlinux}
    frr1:
      kind: linux
      image: quay.io/frrouting/frr:9.1.0
      startup-config: |
        hostname frr1
      labels: {graph-posX: "300", graph-posY: "100"}
    c8k: {kind: cisco_c8000v, image: vrnetlab/cisco_c8000v:17.11}
    br-01: {kind: bridge}
  links:
    - endpoints: ["srl1:e1-1", "frr1:eth1"]
    - endpoints: ["frr1:eth2", "c8k:eth1"]
    - endpoints: ["srl1:e1-2", "br-01:eth1"]
"""

CML = """
lab:
  title: ospf lab
  description: due router
  version: 0.2.2
nodes:
  - id: n0
    label: R1
    node_definition: iosv
    x: -200
    y: 0
    configuration: |-
      hostname R1
    interfaces:
      - {id: i0, label: Loopback0, type: loopback}
      - {id: i1, slot: 0, label: GigabitEthernet0/0, type: physical}
      - {id: i2, slot: 1, label: GigabitEthernet0/1, type: physical}
  - id: n1
    label: R2
    node_definition: cat8000v
    x: 100
    y: 0
    configuration:
      - {name: iosxe_config.txt, content: hostname R2}
    interfaces:
      - {id: i0, slot: 0, label: GigabitEthernet1, type: physical}
  - id: n2
    label: ext-conn-0
    node_definition: external_connector
    x: 0
    y: 200
    interfaces:
      - {id: i0, slot: 0, label: port, type: physical}
links:
  - {id: l0, n1: n0, i1: i1, n2: n1, i2: i0}
  - {id: l1, n1: n0, i1: i2, n2: n2, i2: i0}
"""

GNS3 = {
    "name": "gns3 lab",
    "topology": {
        "nodes": [
            {"node_id": "a" * 8 + "-0000-0000-0000-" + "0" * 12, "name": "R1", "node_type": "qemu", "x": 10, "y": 20,
             "properties": {"hda_disk_image": "vios-adventerprisek9-m.vmdk.SPA.156-2.T", "adapters": 4, "ram": 512},
             "ports": [{"name": "Gi0/0", "adapter_number": 0, "port_number": 0}, {"name": "Gi0/1", "adapter_number": 1, "port_number": 0}]},
            {"node_id": "sw", "name": "Switch 1", "node_type": "ethernet_switch", "x": 200, "y": 20,
             "ports": [{"name": "Ethernet0", "adapter_number": 0, "port_number": 0}, {"name": "Ethernet1", "adapter_number": 0, "port_number": 1}]},
            {"node_id": "pc", "name": "PC1", "node_type": "vpcs", "x": 300, "y": 100,
             "ports": [{"name": "Ethernet0", "adapter_number": 0, "port_number": 0}]},
        ],
        "links": [
            {"nodes": [{"node_id": "a" * 8 + "-0000-0000-0000-" + "0" * 12, "adapter_number": 0, "port_number": 0}, {"node_id": "sw", "adapter_number": 0, "port_number": 0}]},
            {"nodes": [{"node_id": "sw", "adapter_number": 0, "port_number": 1}, {"node_id": "pc", "adapter_number": 0, "port_number": 0}]},
        ],
    },
}

UNL = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<lab name="eve lab" id="x" version="1" author="me" description="da EVE">
  <topology>
    <nodes>
      <node id="1" name="R1" type="qemu" template="vios" image="vios-adventerprisek9-m-15.6.2T" cpu="1" ram="1024" ethernet="4" left="100" top="100" config="1">
        <interface id="0" name="Gi0/0" type="ethernet" network_id="1"/>
        <interface id="1" name="Gi0/1" type="ethernet" network_id="2"/>
      </node>
      <node id="2" name="R2" type="qemu" template="vios" image="vios-adventerprisek9-m-15.6.2T" left="300" top="100">
        <interface id="0" name="Gi0/0" type="ethernet" network_id="1"/>
        <interface id="1" name="Gi0/1" type="ethernet" network_id="2"/>
      </node>
      <node id="3" name="Linux" type="qemu" template="linux" image="linux-ubuntu" left="200" top="300" ethernet="1">
        <interface id="0" name="e0" type="ethernet" network_id="2"/>
      </node>
    </nodes>
    <networks>
      <network id="1" type="bridge" name="Net-R1iface_0" left="0" top="0" visibility="0"/>
      <network id="2" type="bridge" name="LAN" left="200" top="200" visibility="1"/>
    </networks>
  </topology>
  <objects><configs><config id="1">""" + _b64.b64encode(b"hostname R1\n").decode() + """</config></configs></objects>
</lab>
"""


def _imp(filename, data):
    from netso import interop
    return interop.import_file(filename, data, topo.load_templates())


def _links(lab):
    return sorted(tuple(sorted(l.endpoints)) for l in lab.links)


def test_import_containerlab():
    imp = _imp("x.clab.yml", CLAB.encode())
    assert imp.fmt == "clab" and imp.lab.name == "srl-frr"
    t = {n.name: n.template for n in imp.lab.nodes}
    assert t == {"srl1": "nokia-srlinux", "frr1": "frr", "c8k": "cisco-c8000v", "br-01": "bridge"}
    assert next(n for n in imp.lab.nodes if n.name == "srl1").image == "ghcr.io/nokia/srlinux"
    assert _links(imp.lab) == sorted([("frr1:eth1", "srl1:e1-1"), ("c8k:Gi1", "frr1:eth2"), ("br-01:port1", "srl1:e1-2")])
    assert imp.configs == {"frr1": "hostname frr1\n"}
    topo.validate_lab(imp.lab)


def test_import_cml():
    imp = _imp("ospf.yaml", CML.encode())
    assert imp.fmt == "cml" and imp.lab.name == "ospf-lab" and imp.lab.description == "due router"
    t = {n.name: n.template for n in imp.lab.nodes}
    assert t == {"R1": "cisco-iosv", "R2": "cisco-c8000v", "ext-conn-0": "external"}
    assert _links(imp.lab) == sorted([("R1:Gi0/0", "R2:Gi1"), ("R1:Gi0/1", "ext-conn-0:port1")])
    assert imp.configs == {"R1": "hostname R1", "R2": "hostname R2"}
    topo.validate_lab(imp.lab)


def test_import_gns3_project_zip():
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        z.writestr("project.gns3", _json.dumps(GNS3))
    imp = _imp("lab.gns3project", buf.getvalue())
    assert imp.fmt == "gns3"
    t = {n.name: n.template for n in imp.lab.nodes}
    assert t == {"R1": "cisco-iosv", "Switch-1": "bridge", "PC1": "vpc"}
    assert _links(imp.lab) == sorted([("R1:Gi0/0", "Switch-1:port1"), ("PC1:eth1", "Switch-1:port2")])
    assert any("renamed" in w for w in imp.warnings)
    topo.validate_lab(imp.lab)


def test_import_eve_unl():
    imp = _imp("eve.unl", UNL.encode())
    assert imp.fmt == "unl" and imp.lab.author == "me"
    t = {n.name: n.template for n in imp.lab.nodes}
    assert t == {"R1": "cisco-iosv", "R2": "cisco-iosv", "Linux": "linux-vm", "LAN": "bridge"}
    r1 = next(n for n in imp.lab.nodes if n.name == "R1")
    assert r1.image == "vios-adventerprisek9-m-15.6.2T" and r1.startup_config
    assert _links(imp.lab) == sorted([("R1:Gi0/0", "R2:Gi0/0"), ("LAN:port1", "R1:Gi0/1"), ("LAN:port2", "R2:Gi0/1"), ("LAN:port3", "Linux:eth0")])
    assert imp.configs == {"R1": "hostname R1\n"}
    topo.validate_lab(imp.lab)


def test_import_overrides_and_netso_yaml():
    from netso import interop
    imp = interop.import_file("x.clab.yml", CLAB.encode(), topo.load_templates(), {"srl1": "linux-host"}, "mio-lab")
    assert imp.lab.name == "mio-lab" and next(n for n in imp.lab.nodes if n.name == "srl1").template == "linux-host"
    assert _imp("lab.yml", EXAMPLE.encode()).fmt == "netso"
    with pytest.raises(interop.ImportError_):
        _imp("x.txt", b"ciao: mondo")


def test_import_export_api():
    c = _client()
    data = _b64.b64encode(CML.encode()).decode()
    p = c.post("/api/import/preview", json={"filename": "lab.yaml", "data": data}).json()
    assert p["format"] == "cml" and p["name"] == "ospf-lab" and len(p["nodes"]) == 3 and p["links"] == 2 and not p["exists"]
    c.post("/api/folders", json={"path": "Import"})
    r = c.post("/api/import", json={"filename": "lab.yaml", "data": data, "folder": "Import"})
    assert r.status_code == 201
    assert c.post("/api/import", json={"filename": "lab.yaml", "data": data}).status_code == 409
    s = next(l for l in c.get("/api/labs").json() if l["name"] == "ospf-lab")
    assert s["folder"] == "Import" and s["nodes"] == 3
    assert c.get("/api/labs/ospf-lab/configs").json()["sets"]["default"]["R1"].startswith("hostname R1")
    # export round trip through containerlab and CML
    cl = c.get("/api/labs/ospf-lab/export", params={"format": "clab"})
    assert cl.status_code == 200 and "ospf-lab.clab.yml" in cl.headers["content-disposition"]
    back = _imp("x.clab.yml", cl.text.encode())
    assert {n.template for n in back.lab.nodes} >= {"cisco-iosv", "cisco-c8000v"}
    cm = c.get("/api/labs/ospf-lab/export", params={"format": "cml"}).text
    back = _imp("x.yaml", cm.encode())
    assert _links(back.lab) == sorted([("R1:Gi0/0", "R2:Gi1"), ("R1:Gi0/1", "ext-conn-0:port1")])
    assert c.get("/api/labs/ospf-lab/export", params={"format": "pdf"}).status_code == 400


def test_snapshots_dry_run():
    c = _client()
    c.post("/api/labs", json={"yaml": EXAMPLE})
    node_dir = topo.lab_dir("t1") / "nodes" / "r1"
    node_dir.mkdir(parents=True)
    (node_dir / "disk0.qcow2").write_text("x")
    r = c.post("/api/labs/t1/snapshots", json={"name": "base", "description": "prima"})
    assert r.status_code == 201 and r.json()["disks"] == ["nodes/r1/disk0.qcow2"]
    assert c.post("/api/labs/t1/snapshots", json={"name": "base"}).status_code == 400
    lab = c.get("/api/labs/t1").json()["lab"]
    lab["description"] = "cambiata"
    c.put("/api/labs/t1", json=lab)
    (topo.lab_dir("t1") / "nodes" / "r2").mkdir()
    (topo.lab_dir("t1") / "nodes" / "r2" / "disk0.qcow2").write_text("y")
    assert c.post("/api/labs/t1/snapshots/base/restore").status_code == 200
    assert c.get("/api/labs/t1").json()["lab"]["description"] == ""
    from netso.engine import engine
    log = "\n".join(engine.r.log)
    assert "qemu-img snapshot -c netso-base" in log and "qemu-img snapshot -a netso-base" in log
    assert f"rm {topo.lab_dir('t1') / 'nodes' / 'r2' / 'disk0.qcow2'}" in log  # disk created after the snapshot
    assert [s["name"] for s in c.get("/api/labs/t1/snapshots").json()] == ["base"]
    assert c.delete("/api/labs/t1/snapshots/base").status_code == 200
    assert c.get("/api/labs/t1/snapshots").json() == []


# ------------------------------------------------------------------ device catalog & images
CML_NODE = """
id: iosv
general: {nature: router, description: Cisco IOSv router}
device:
  interfaces:
    has_loopback_zero: true
    physical: [GigabitEthernet0/0, GigabitEthernet0/1, GigabitEthernet0/2, GigabitEthernet0/3]
    default_count: 4
    loopback: [Loopback0]
ui: {visible: true, label_prefix: iosv-, icon: router, label: IOSv, description: "IOSv 15.9"}
sim:
  linux_native: {libvirt_domain_driver: kvm, driver: iosv, disk_driver: virtio, ram: 512, cpus: 1, nic_driver: e1000}
configuration:
  provisioning:
    volume_name: ios_config
    media_type: raw
    files: [{name: ios_config.txt, content: "", editable: true}]
schema_version: 0.0.1
"""
CML_IMAGE = """
id: iosv-159-3-m3
label: IOSv 15.9(3)M3
node_definition_id: iosv
disk_image: vios-adventerprisek9-m.spa.159-3.m3.qcow2
schema_version: 0.0.1
"""
GNS3A = {
    "appliance_id": "x", "name": "Cisco ASAv", "category": "firewall", "vendor_name": "Cisco", "registry_version": 4,
    "description": "Adaptive Security Virtual Appliance", "port_name_format": "Gi0/{0}", "first_port_name": "Management0/0",
    "qemu": {"adapter_type": "e1000", "adapters": 4, "ram": 2048, "hda_disk_interface": "virtio", "console_type": "telnet", "kvm": "require"},
    "images": [{"filename": "asav9-18-2.qcow2", "version": "9.18.2", "md5sum": "0123456789abcdef0123456789abcdef", "filesize": 10}],
    "versions": [{"name": "9.18.2", "images": {"hda_disk_image": "asav9-18-2.qcow2"}}],
}


def test_catalog_import_cml_and_gns3():
    from netso import catalog
    res = catalog.parse_definitions([("iosv.yaml", CML_NODE.encode()), ("img.yaml", CML_IMAGE.encode()),
                                     ("asav.gns3a", _json.dumps(GNS3A).encode()), ("x.txt", b"ciao")])
    t = {x.name: x for x in res["templates"]}
    iosv = t["iosv"]
    assert iosv.kind == "qemu" and iosv.ram == 512 and iosv.nic_model == "e1000" and iosv.image == "iosv-159-3-m3"
    assert iosv.interface_names()[:2] == ["GigabitEthernet0/0", "GigabitEthernet0/1"] and iosv.interface_names(6)[5] == "GigabitEthernet0/5"
    assert (iosv.config_method, iosv.config_file, iosv.config_label) == ("disk", "ios_config.txt", "ios_config")
    asav = t["cisco-asav"]
    assert asav.icon == "firewall" and asav.ram == 2048 and asav.interface_names() == ["Management0/0", "Gi0/0", "Gi0/1", "Gi0/2"]
    assert asav.image_files[0]["md5"] == "0123456789abcdef0123456789abcdef" and asav.image == "cisco-asav-9-18-2"
    assert any("x.txt" in w for w in res["warnings"])


def test_template_crud_api():
    c = _client()
    tpl = {"name": "my-router", "kind": "qemu", "image": "my-img", "nics": 3, "interfaces": ["ge-0/0/0", "ge-0/0/1"], "icon": "router"}
    assert c.post("/api/templates", json=tpl).status_code == 201
    assert c.post("/api/templates", json=tpl).status_code == 409
    got = next(t for t in c.get("/api/templates").json() if t["name"] == "my-router")
    assert got["origin"] == "utente" and got["iface_names"] == ["ge-0/0/0", "ge-0/0/1", "ge-0/0/2"]
    c.post("/api/labs", json={"yaml": "name: u1\nnodes: [{name: r1, template: my-router}]\nlinks: []\n"})
    assert c.put("/api/templates/my-router", json={**tpl, "name": "renamed"}).status_code == 400  # used in a lab
    assert c.put("/api/templates/my-router", json={**tpl, "ram": 4096}).status_code == 200
    assert c.delete("/api/templates/my-router").status_code == 400
    c.delete("/api/labs/u1")
    assert c.delete("/api/templates/my-router").json()["result"] == "eliminato"
    # editing a built-in creates an override; deleting it restores the original
    alp = next(t for t in c.get("/api/templates").json() if t["name"] == "alpine")
    assert c.put("/api/templates/alpine", json={**{k: v for k, v in alp.items() if k not in ("iface_names", "origin")}, "ram": 999}).status_code == 200
    assert next(t for t in c.get("/api/templates").json() if t["name"] == "alpine")["origin"] == "modificato"
    assert c.delete("/api/templates/alpine").json()["result"] == "ripristinato"
    assert c.delete("/api/templates/cisco-iosv").status_code == 400
    # import through the API
    files = [{"filename": "asav.gns3a", "data": _b64.b64encode(_json.dumps(GNS3A).encode()).decode()}]
    p = c.post("/api/templates/import", json={"files": files}).json()
    assert [t["name"] for t in p["templates"]] == ["cisco-asav"] and p["saved"] == []
    assert c.post("/api/templates/import", json={"files": files, "save": True}).json()["saved"] == ["cisco-asav"]
    assert any(t["name"] == "cisco-asav" for t in c.get("/api/templates").json())


def test_container_templates_and_shell():
    t = topo.load_templates()
    assert t["nokia-srlinux"].interface_names()[:2] == ["e1-1", "e1-2"] and t["nokia-srlinux"].shell == ["sr_cli"]
    from netso.drivers.docker import DockerDriver
    from netso.models import NodeState
    assert DockerDriver.console_command(NodeState(kind="docker", container="c"), ["sr_cli"])[-1] == "sr_cli"


def test_images_upload_convert_checksum_delete():
    import hashlib
    import time as _t
    from netso import images as imgs
    c = _client()
    data = b"fake disk" * 100
    r = c.put("/api/images/vios-test/upload", params={"filename": "vios.vmdk"}, content=data)
    assert r.status_code == 200 and r.json()["job"]
    for _ in range(50):
        jobs = c.get("/api/jobs").json()
        if all(j["status"] != "running" for j in jobs):
            break
        _t.sleep(0.05)
    assert jobs[0]["status"] == "done", jobs
    from netso.engine import engine
    assert any("qemu-img convert -O qcow2 -f vmdk" in l for l in engine.r.log)
    img = next(i for i in c.get("/api/images").json() if i["name"] == "vios-test")
    assert img["files"][0]["name"] == "vios.vmdk"  # dry-run: conversion only logged
    # checksum verified against a template's expected files
    md5 = hashlib.md5(data).hexdigest()
    c.post("/api/templates", json={"name": "vt", "kind": "qemu", "image": "vios-test", "image_files": [{"filename": "vios.vmdk", "md5": md5, "version": "1.0"}]})
    job = c.post("/api/images/vios-test/checksum").json()["job"]
    for _ in range(50):
        if next(j for j in c.get("/api/jobs").json() if j["id"] == job)["status"] != "running":
            break
        _t.sleep(0.05)
    f = next(i for i in c.get("/api/images").json() if i["name"] == "vios-test")["files"][0]
    assert f["md5"] == md5 and f["verified"] == {"template": "vt", "version": "1.0"}
    assert "template vt" in next(i for i in c.get("/api/images").json() if i["name"] == "vios-test")["used_by"]
    # bad names, in-use guard, delete
    assert c.put("/api/images/..%2Fetc/upload", params={"filename": "x"}, content=b"x").status_code in (400, 404)
    assert c.put("/api/images/ok/upload", params={"filename": "../x"}, content=b"x").status_code == 400
    c.post("/api/labs", json={"yaml": "name: u2\nnodes: [{name: r1, template: vt, image: vios-test}]\nlinks: []\n"})
    assert c.delete("/api/images/vios-test").status_code == 400
    assert c.delete("/api/images/vios-test", params={"force": True}).status_code == 200
    assert not (settings.images / "vios-test").exists()
    with pytest.raises(imgs.ImageError):
        imgs.finalize(engine.r, settings.images / "x.exe")


def test_refplat_iso(tmp_path):
    import shutil as _sh
    import subprocess as _sp
    if not _sh.which("bsdtar"):
        pytest.skip("bsdtar non disponibile")
    src = tmp_path / "iso"
    (src / "node-definitions").mkdir(parents=True)
    (src / "virl-base-images" / "iosv-159-3-m3").mkdir(parents=True)
    (src / "node-definitions" / "iosv.yaml").write_text(CML_NODE)
    (src / "virl-base-images" / "iosv-159-3-m3" / "iosv-159-3-m3.yaml").write_text(CML_IMAGE)
    (src / "virl-base-images" / "iosv-159-3-m3" / "vios-adventerprisek9-m.spa.159-3.m3.qcow2").write_bytes(b"QFI")
    iso_dir = settings.images / "_iso"
    iso_dir.mkdir(parents=True)
    _sp.run(["bsdtar", "-cf", str(iso_dir / "refplat.iso"), "-C", str(src), "."], check=True)  # a tar stands in for the ISO
    c = _client()
    assert c.get("/api/images/iso").json()[0]["name"] == "refplat.iso"
    scan = c.get("/api/images/refplat/refplat.iso").json()
    assert scan["images"][0]["id"] == "iosv-159-3-m3" and not scan["images"][0]["present"]
    job = c.post("/api/images/refplat/refplat.iso", json={"images": ["iosv-159-3-m3"]}).json()["job"]
    import time as _t
    for _ in range(100):
        j = next(x for x in c.get("/api/jobs").json() if x["id"] == job)
        if j["status"] != "running":
            break
        _t.sleep(0.05)
    assert j["status"] == "done", j
    assert (settings.images / "iosv-159-3-m3" / "vios-adventerprisek9-m.spa.159-3.m3.qcow2").read_bytes() == b"QFI"
    assert topo.load_templates()["iosv"].image == "iosv-159-3-m3"


# ------------------------------------------------------------------ switch VLANs, NAT cloud, VPC, drawings
VLAN_LAB = """
name: v1
nodes:
  - name: sw
    template: bridge
    ports:
      port1: {mode: access, vlan: 10}
      port2: {mode: trunk, vlans: "10,20-30", native: 99}
  - {name: pc1, template: vpc}
  - {name: r1, template: cisco-iosv}
  - {name: inet, template: nat, subnet: 10.99.0.0/24}
links:
  - endpoints: [pc1:eth1, sw:port1]
  - endpoints: [r1:Gi0/0, sw:port2]
  - endpoints: [r1:Gi0/1, inet:port1]
"""


def test_vlans_nat_vpc_dry_run():
    r = Runner(force_dry_run=True)
    lab = topo.parse_lab_yaml(VLAN_LAB)
    topo.save_lab(lab)
    eng = Engine(r)
    st = eng.deploy("v1")
    assert all(n.status == "running" for n in st.nodes.values()), {k: v.error for k, v in st.nodes.items()}
    log = "\n".join(r.log)
    from netso import network as net
    br = net.node_bridge_name("v1", "sw")
    assert f"ip link set {br} type bridge vlan_filtering 1" in log
    pc_if, r1_if = st.nodes["pc1"].veths["eth1"], st.nodes["r1"].taps["Gi0/0"]
    assert f"bridge vlan add dev {pc_if} vid 10 pvid untagged" in log
    assert f"bridge vlan add dev {r1_if} vid 10" in log and f"bridge vlan add dev {r1_if} vid 20-30" in log
    assert f"bridge vlan add dev {r1_if} vid 99 pvid untagged" in log
    # NAT cloud: gateway, masquerade, forward, dnsmasq
    nat = st.nodes["inet"]
    assert nat.subnet == "10.99.0.0/24" and f"ip addr replace 10.99.0.1/24 dev {nat.bridge}" in log
    assert "MASQUERADE" in log and "--dhcp-range=10.99.0.10,10.99.0.254,12h" in log
    assert f"/sys/class/net/{br}/bridge/nf_call_iptables" in log
    # VPC: helper script copied before the first start, boots with --boot
    assert "vpc.sh" in log and "/usr/local/bin/vpc --boot" in log
    eng.destroy("v1")
    assert "kill 4343" in "\n".join(r.log) and "iptables -t nat -D POSTROUTING" in "\n".join(r.log)


def test_bad_vlan_and_subnet():
    for bad in ["ports: {port1: {mode: access, vlan: 5000}}", "ports: {port1: {mode: hybrid}}", "ports: {port1: {mode: trunk, vlans: '30-10'}}"]:
        with pytest.raises(topo.TopologyError):
            topo.parse_lab_yaml("name: b1\nnodes: [{name: sw, template: bridge, " + bad + "}]")
    with pytest.raises(topo.TopologyError):
        topo.parse_lab_yaml("name: b2\nnodes: [{name: n, template: nat, subnet: 10.0.0.0/30}]")


def test_annotations_roundtrip_and_import():
    lab = topo.parse_lab_yaml("""
name: d1
nodes: []
annotations:
  - {id: t1, type: text, x: 10, y: 20, text: "Area 0", font_size: 18, bold: true}
  - {id: r1, type: rect, x: 0, y: 0, w: 300, h: 200, fill: "#22d3ee", opacity: 0.2, border: dashed}
  - {id: l1, type: line, x: 0, y: 0, w: 100, h: 50, arrow: both}
""")
    text = topo.dump_lab_yaml(lab)
    assert "Area 0" in text and "border: dashed" in text and "font_size: 18" in text
    assert topo.parse_lab_yaml(text).annotations[1].fill == "#22d3ee"
    with pytest.raises(topo.TopologyError):
        topo.parse_lab_yaml("name: d2\nannotations: [{id: x, type: image, image: 'http://evil'}]")
    from netso import interop
    cml = CML.replace("links:", """annotations:
  - {type: text, x1: 5, y1: 6, text_content: "Core", text_size: 14, color: "#FF0000FF", z_index: 1}
  - {type: rectangle, x1: 0, y1: 0, x2: 400, y2: 300, color: "#00FF0040", border_color: "#000000", border_style: "2,2", thickness: 2, border_radius: 4, z_index: 0}
  - {type: ellipse, x1: 100, y1: 100, x2: 50, y2: 25, color: "#0000FF", border_color: "#000000", thickness: 1, z_index: 0}
  - {type: line, x1: 300, y1: 0, x2: 200, y2: 100, line_end: arrow, border_color: "#123456", thickness: 3}
links:""")
    imp = interop.import_file("x.yaml", cml.encode(), topo.load_templates())
    a = {x.type: x for x in imp.lab.annotations}
    assert a["text"].text == "Core" and a["text"].color == "#ff0000" and a["text"].z == 1
    assert (a["rect"].w, a["rect"].border) == (400, "dotted")
    assert (a["ellipse"].x, a["ellipse"].w, a["ellipse"].h) == (50, 100, 50)
    assert a["line"].flip and a["line"].arrow == "end" and a["line"].color == "#123456"
    out = interop.export_cml(imp.lab, topo.load_templates(), {})[0]
    back = interop.import_file("y.yaml", out.encode(), topo.load_templates())
    assert sorted(x.type for x in back.lab.annotations) == ["ellipse", "line", "rect", "text"]
    gns = {"name": "g", "topology": {"nodes": [{"node_id": "a", "name": "R", "node_type": "vpcs", "x": 0, "y": 0, "ports": []}], "links": [],
           "drawings": [{"x": 10, "y": 10, "z": 1, "svg": '<svg width="200" height="40"><text fill="#00ff00" font-size="16" font-weight="bold">Hello</text></svg>'},
                        {"x": 0, "y": 0, "z": 0, "svg": '<svg width="100" height="50"><ellipse cx="50" cy="25" rx="50" ry="25" fill="#ff00ff" fill-opacity="0.5" stroke="#000000" stroke-width="2"/></svg>'}]}}
    g = interop.import_file("p.gns3", _json.dumps(gns).encode(), topo.load_templates()).lab.annotations
    assert g[0].type == "text" and g[0].text == "Hello" and g[0].bold and g[1].type == "ellipse" and g[1].opacity == 0.5


# ------------------------------------------------------------------ automation: save configs, inventory, generators
def _fake_ios(port_holder, ready):
    """A tiny serial console speaking telnet, IOS style: R1> enable R1# show running-config."""
    import socket as _s
    srv = _s.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port_holder.append(srv.getsockname()[1])
    ready.set()
    conn, _ = srv.accept()
    conn.sendall(bytes([255, 251, 1, 255, 251, 3]) + b"\r\n")  # IAC WILL ECHO, WILL SGA
    prompt = b"R1>"
    buf = b""
    cfg = b"Building configuration...\r\n\r\nCurrent configuration : 120 bytes\r\n!\r\nhostname R1\r\n!\r\ninterface Gi0/0\r\n ip address 10.0.0.1 255.255.255.0\r\n--More-- "
    while True:
        data = conn.recv(1024)
        if not data:
            break
        buf += bytes(b for b in data if b < 240)
        while b"\r" in buf or b" " == buf:
            if buf == b" ":  # answer to --More--
                conn.sendall(b"\r\n!\r\nend\r\n" + prompt)
                buf = b""
                break
            line, _, buf = buf.partition(b"\r")
            line = line.strip()
            if line == b"enable":
                prompt = b"R1#"
                conn.sendall(b"enable\r\n" + prompt)
            elif line == b"terminal length 0":
                conn.sendall(b"terminal length 0\r\n" + prompt)
            elif line == b"show running-config":
                conn.sendall(b"show running-config\r\n" + cfg)
            else:
                conn.sendall(b"\r\n" + prompt)
    conn.close()
    srv.close()


def test_console_config_scraper():
    import threading
    from netso import savecfg
    port, ready = [], threading.Event()
    threading.Thread(target=_fake_ios, args=(port, ready), daemon=True).start()
    ready.wait(2)
    cfg = savecfg.console_config("127.0.0.1", port[0], ["terminal length 0", "show running-config"], enable=True, boot_wait=5)
    assert cfg.startswith("!\nhostname R1") and "ip address 10.0.0.1" in cfg and cfg.rstrip().endswith("end")
    assert "Building configuration" not in cfg and "R1#" not in cfg and "More" not in cfg
    with pytest.raises(savecfg.SaveError):
        savecfg.console_config("127.0.0.1", 1, ["x"], boot_wait=1)


def test_save_configs_and_inventory_dry_run():
    import time as _t
    c = _client()
    yaml_lab = """
name: a1
nodes:
  - {name: r1, template: cisco-iosv, mgmt_iface: Gi0/3, mgmt_ip: 172.20.20.11}
  - {name: f1, template: frr, mgmt_ip: 172.20.20.21}
  - {name: pc, template: vpc}
  - {name: sw, template: bridge}
links:
  - endpoints: [r1:Gi0/0, f1:eth1]
  - endpoints: [pc:eth1, sw:port1]
"""
    c.post("/api/labs", json={"yaml": yaml_lab})
    assert c.post("/api/labs/a1/deploy").status_code == 200
    from netso.engine import engine
    log = "\n".join(engine.r.log)
    assert "--ip 172.20.20.21" in log and "--subnet 172.20.20.0/24" in log
    assert "br-0123456789ab" in log  # r1 Gi0/3 plugged into the management network
    r = c.post("/api/labs/a1/save-configs", json={"set": "salvate", "new_set": True}).json()
    for _ in range(100):
        j = c.get(f"/api/jobs/{r['job']}").json()
        if j["status"] != "running":
            break
        _t.sleep(0.05)
    assert j["status"] == "done", j
    res = j["result"]["results"]
    assert res["r1"] == "ok" and res["f1"] == "ok" and res["pc"] == "ok" and "sw" not in res
    sets = c.get("/api/labs/a1/configs").json()["sets"]
    assert "hostname r1" in sets["salvate"]["r1"] and "hostname dry-run" in sets["salvate"]["f1"]
    assert c.post("/api/labs/a1/save-configs", json={"set": "nope"}).status_code == 400
    rows = {x["name"]: x for x in c.get("/api/labs/a1/inventory").json()}
    assert rows["r1"]["mgmt_ip"] == "172.20.20.11" and rows["f1"]["mgmt_ip"] == "172.20.20.2" and "sw" not in rows
    inv = c.get("/api/labs/a1/inventory", params={"format": "ansible"}).text
    assert "ansible_network_os: cisco.ios.ios" in inv and "ansible_host: 172.20.20.11" in inv
    assert "172.20.20.11\tr1 r1.a1" in c.get("/api/labs/a1/inventory", params={"format": "hosts"}).text
    assert "Host f1.a1" in c.get("/api/labs/a1/inventory", params={"format": "ssh"}).text
    with pytest.raises(topo.TopologyError):  # management NIC already used by a link
        topo.validate_lab(topo.parse_lab_yaml(yaml_lab.replace("mgmt_iface: Gi0/3", "mgmt_iface: Gi0/0")))


def test_generators():
    from netso import automation
    t = topo.load_templates()
    lab = topo.parse_lab_yaml("name: g1\nnodes: [{name: spine1, template: frr}]\nlinks: []\n")
    g = automation.generate(lab, t, "clos", {"spines": 2, "leaves": 3, "hosts": 2})
    names = [n["name"] for n in g["nodes"]]
    assert "spine1" not in names and "spine2" in names and "spine3" in names  # existing name skipped
    assert len([n for n in names if n.startswith("leaf")]) == 3 and len(g["links"]) == 2 * 3 + 3 * 2
    merged = topo.parse_lab_yaml(topo.dump_lab_yaml(lab))
    from netso.models import Link as _L, Node as _N
    merged.nodes += [_N(**n) for n in g["nodes"]]
    merged.links += [_L(**l) for l in g["links"]]
    topo.validate_lab(merged)
    ring = automation.generate(Lab(name="x"), t, "ring", {"count": 5, "tpl": "cisco-iosv"})
    assert len(ring["links"]) == 5
    mesh = automation.generate(Lab(name="x"), t, "mesh", {"count": 6, "tpl": "cisco-iosv"})
    assert len(mesh["links"]) == 15 and any(n.get("nics") == 5 for n in mesh["nodes"])  # iosv has 4 NICs: grown to 5
    star = automation.generate(Lab(name="x"), t, "star", {"count": 3})
    assert len(star["links"]) == 3 and star["nodes"][0]["template"] == "bridge"
    assert len(automation.generate(Lab(name="x"), t, "chain", {"count": 4})["links"]) == 3
    with pytest.raises(automation.GenerateError):
        automation.generate(Lab(name="x"), t, "star", {"count": 2, "tpl": "nat"})
    with pytest.raises(automation.GenerateError):
        automation.generate(Lab(name="x"), t, "hexagon", {})
    c = _client()
    c.post("/api/labs", json={"name": "g2"})
    assert len(c.post("/api/labs/g2/generate", json={"pattern": "ring", "params": {"count": 3}}).json()["links"]) == 3


# ------------------------------------------------------------------ phase 5: users & sharing, VXLAN, servers, Dynamips
def test_roles_and_sharing(monkeypatch):
    monkeypatch.setattr(settings, "auth", True)
    c = _client()
    adm = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "Netso"}).json()["token"]}
    for u, role in [("anna", "user"), ("bob", "user"), ("vera", "viewer")]:
        assert c.post("/api/users", headers=adm, json={"username": u, "password": "pw1234", "role": role}).status_code == 201
    tok = lambda u: {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": u, "password": "pw1234"}).json()["token"]}  # noqa: E731
    anna, bob, vera = tok("anna"), tok("bob"), tok("vera")
    assert c.get("/api/auth/me", headers=vera).json()["role"] == "viewer"
    # anna creates a lab: hers; bob and vera do not see it
    assert c.post("/api/labs", headers=anna, json={"name": "lab-anna"}).status_code == 201
    assert [l["name"] for l in c.get("/api/labs", headers=anna).json()] == ["lab-anna"]
    assert c.get("/api/labs", headers=bob).json() == [] and c.get("/api/labs/lab-anna", headers=bob).status_code == 403
    assert c.post("/api/labs", headers=vera, json={"name": "nope"}).status_code == 403  # viewer cannot create
    assert c.post("/api/templates", headers=anna, json={"name": "x1", "kind": "docker", "image": "a"}).status_code == 403  # admin only
    assert c.post("/api/users", headers=anna, json={"username": "z", "password": "pw1234"}).status_code == 403
    assert c.get("/api/users", headers=anna).status_code == 200  # names for the share dialog
    # share: bob edit, vera view (vera stays read-only even if given edit)
    assert c.put("/api/labs/lab-anna/share", headers=bob, json={"shared": {"bob": "edit"}}).status_code == 403
    assert c.put("/api/labs/lab-anna/share", headers=anna, json={"shared": {"bob": "edit", "vera": "edit", "ghost": "view"}}).status_code == 400
    assert c.put("/api/labs/lab-anna/share", headers=anna, json={"shared": {"bob": "edit", "vera": "edit"}}).status_code == 200
    lb = c.get("/api/labs", headers=bob).json()[0]
    assert lb["access"] == "edit" and lb["owner"] == "anna"
    assert c.get("/api/labs/lab-anna", headers=vera).json()["access"] == "view"
    lab = c.get("/api/labs/lab-anna", headers=bob).json()["lab"]
    lab["description"] = "da bob"
    assert c.put("/api/labs/lab-anna", headers=bob, json=lab).status_code == 200
    assert c.put("/api/labs/lab-anna", headers=vera, json=lab).status_code == 403
    assert c.post("/api/labs/lab-anna/deploy", headers=vera).status_code == 403
    assert c.delete("/api/labs/lab-anna", headers=bob).status_code == 403  # only the owner
    with pytest.raises(Exception):  # console websocket refused to a viewer
        with c.websocket_connect(f"/api/labs/lab-anna/nodes/x/console?token={vera['Authorization'][7:]}") as ws:
            ws.receive_text()
    # roles: promote, last admin protected, deleting a user hands labs back to admin
    assert c.put("/api/users/vera", headers=adm, json={"username": "vera", "role": "user"}).status_code == 200
    assert c.put("/api/users/admin", headers=adm, json={"username": "admin", "role": "user"}).status_code == 400
    assert c.delete("/api/users/anna", headers=adm).status_code == 200
    assert next(l for l in c.get("/api/labs", headers=adm).json() if l["name"] == "lab-anna")["owner"] == "admin"
    assert c.delete("/api/labs/lab-anna", headers=adm).status_code == 200


def test_vxlan_dynamips_servers_dry_run():
    r = Runner(force_dry_run=True)
    lab = topo.parse_lab_yaml("""
name: x5
nodes:
  - {name: r1, template: cisco-c7200}
  - {name: sw, template: cisco-c3725}
  - {name: dc2, template: vxlan, remote: 192.168.12.50, vni: 5001}
links:
  - endpoints: [r1:Fa1/0, dc2:port1]
  - endpoints: [r1:Fa0/0, sw:Fa2/3]
""")
    topo.save_lab(lab)
    topo.write_config("x5", "default", "r1", "hostname r1")
    lab.nodes[0].startup_config = True
    topo.save_lab(lab)
    eng = Engine(r)
    st = eng.deploy("x5")
    assert all(n.status == "running" for n in st.nodes.values()), {k: v.error for k, v in st.nodes.items()}
    log = "\n".join(r.log)
    from netso import network as net
    assert f"type vxlan id 5001 remote 192.168.12.50 dstport 4789" in log and f"master {net.node_bridge_name('x5', 'dc2')}" in log
    dyn = next(l for l in r.log if "dynamips -P 7200" in l)
    assert "-p 1:PA-2FE-TX" in dyn and "-s 1:0:tap:" in dyn and "-C" in dyn and "-T " in dyn
    dyn2 = next(l for l in r.log if "dynamips -P 3725" in l)
    assert "-p 0:GT96100-FE" not in dyn2 and "-p 2:NM-16ESW" in dyn2 and "-s 2:3:tap:" in dyn2
    assert st.nodes["r1"].console_port and eng.console_command("x5", "r1")[0] == "telnet"
    eng.destroy("x5")
    assert f"ip link del {net.vxlan_name('x5', 'dc2')}" in "\n".join(r.log)
    with pytest.raises(topo.TopologyError):
        topo.validate_lab(topo.parse_lab_yaml("name: x6\nnodes: [{name: v, template: vxlan}]"))
    # known servers
    c = _client()
    assert c.post("/api/servers", json={"name": "lab2", "url": "http://127.0.0.1:9"}).status_code == 201
    assert c.post("/api/servers", json={"name": "bad", "url": "ftp://x"}).status_code == 400
    assert c.get("/api/servers").json()[0]["host"] == "127.0.0.1"
    assert c.get("/api/servers/lab2/ping").json()["online"] is False
    assert c.delete("/api/servers/lab2").status_code == 200 and c.get("/api/servers").json() == []


def test_import_images_from_pnetlab(tmp_path):
    from netso import pnetimport
    from netso.jobs import Job
    root = tmp_path / "unetlab"
    q = root / "addons" / "qemu"
    for folder, disk in [("vios-adventerprisek9-m.SPA.159-3.M6", "virtioa.qcow2"), ("asav-981", "virtioa.qcow2"),
                         ("asav-9201", "virtioa.qcow2"), ("nxosv9k-9.3.9", "sataa.qcow2"), ("ccie-ubuntu22", "virtioa.qcow2"),
                         ("empty-1", "readme.txt")]:
        (q / folder).mkdir(parents=True)
        (q / folder / disk).write_bytes(b"x" * 10)
    (root / "addons" / "dynamips").mkdir()
    (root / "addons" / "dynamips" / "c7200-adventerprisek9-mz.152-4.M7.image").write_bytes(b"ios")
    (root / "addons" / "iol" / "bin").mkdir(parents=True)
    (root / "addons" / "iol" / "bin" / "i86bi-linux-l3.bin").write_bytes(b"iol")
    (root / "addons" / "iol" / "bin" / "iourc").write_text("[license]\npnetlab = 0123456789abcdef;\n")
    (root / "addons" / "iol" / "lib").mkdir()
    (root / "addons" / "iol" / "lib" / "libcrypto.so.4").write_bytes(b"lib")
    tdir = root / "html" / "templates" / "intel"
    tdir.mkdir(parents=True)
    (tdir / "asav.yml").write_text("type: qemu\nname: ASAv\ndescription: Cisco ASAv\nicon: Firewall.png\ncpu: 1\nram: 2048\n"
                                   "ethernet: 8\neth_format: Gi0/{0}\nconsole: telnet\nqemu_nic: vmxnet3\n")
    r = Runner(force_dry_run=True)
    found = pnetimport.scan(str(root / "addons"), r)
    by = {i["folder"]: i for i in found["items"]}
    assert set(by) == {"vios-adventerprisek9-m.SPA.159-3.M6", "asav-981", "asav-9201", "nxosv9k-9.3.9", "c7200-adventerprisek9-mz.152-4.M7",
                       "iol-i86bi-linux-l3", "ccie-ubuntu22"}
    assert by["iol-i86bi-linux-l3"]["template"] == "cisco-iol"
    assert by["vios-adventerprisek9-m.SPA.159-3.M6"]["template"] == "cisco-iosv"
    assert by["c7200-adventerprisek9-mz.152-4.M7"]["template"] == "cisco-c7200"
    assert found["notes"] and "is for 'pnetlab'" in found["notes"][0] and found["iourc"] is None  # another host's license: not copied
    assert pnetimport.scan(str(root), r)["items"]  # /opt/unetlab works too
    import socket
    mine = root / "mine"
    (mine / "iol" / "bin").mkdir(parents=True)
    (mine / "iol" / "bin" / "a.bin").write_bytes(b"iol")
    (mine / "iol" / "bin" / "iourc").write_text(f"[license]\n{socket.gethostname()} = 0123456789abcdef;\n")
    assert pnetimport.scan(str(mine), r)["iourc"]  # a license for this very host is taken along
    from_qemu = pnetimport.scan(str(q), r)  # addons/qemu: IOL and dynamips are found next to it
    assert {"iol", "dynamips", "qemu"} <= {i["kind"] for i in from_qemu["items"]} and from_qemu["iol_lib"]

    import collections
    real = pnetimport.shutil.disk_usage
    pnetimport.shutil.disk_usage = lambda p: collections.namedtuple("U", "total used free")(10, 9, 1 << 30)
    try:
        with pytest.raises(pnetimport.PnetError, match="Not enough space"):
            pnetimport.run_import(r, Job(id="t", kind="pnet", label="x"), str(root / "addons"), None, False)
    finally:
        pnetimport.shutil.disk_usage = real
    msg = pnetimport.run_import(r, Job(id="t", kind="pnet", label="x"), str(root / "addons"), None, False)
    assert "7 images" in msg
    assert (settings.images / "iol-i86bi-linux-l3" / "i86bi-linux-l3.bin").exists()
    assert "IOL libraries: libcrypto.so.4" in msg and (settings.home / "iol-lib" / "libcrypto.so.4").exists()
    assert (settings.images / "asav-981" / "virtioa.qcow2").exists()
    assert (settings.images / "c7200-adventerprisek9-mz.152-4.M7" / "c7200-adventerprisek9-mz.152-4.M7.image").exists()
    t = topo.load_templates()
    assert t["cisco-iosv"].image == "vios-adventerprisek9-m.SPA.159-3.M6"
    assert t["cisco-c7200"].image == "c7200-adventerprisek9-mz.152-4.M7"
    assert t["cisco-iol"].image == "iol-i86bi-linux-l3"
    asa = t["asav"]
    assert (asa.image, asa.icon, asa.nics, asa.ram, asa.nic_model) == ("asav-981", "firewall", 8, 2048, "vmxnet3")
    assert asa.interface_names()[:2] == ["Gi0/0", "Gi0/1"]
    assert t["nxosv9k"].disk_bus == "sata" and t["nxosv9k"].icon == "l3switch"
    assert (t["ccie"].icon, t["ccie"].console, t["ccie"].interface_names()[0]) == ("server", "vnc", "eth0")  # a Linux VM
    # again: everything is already there
    assert "Nothing to import" in pnetimport.run_import(r, Job(id="t", kind="pnet", label="x"), str(root / "addons"), None, False)
    # a Linux VM taken for a router by an older import is fixed
    from netso import catalog
    catalog.save_template(t["ccie"].model_copy(update={"icon": "router", "console": "telnet"}))
    assert "linked: ccie" in pnetimport.run_import(r, Job(id="t", kind="pnet", label="x"), str(root / "addons"), None, False)
    assert (topo.load_templates()["ccie"].icon, topo.load_templates()["ccie"].console) == ("server", "vnc")
    # a run that stopped after copying (no devices yet) is completed by the next one
    from netso import catalog
    catalog.user_file("asav").unlink()
    assert "new devices: asav" in pnetimport.run_import(r, Job(id="t", kind="pnet", label="x"), str(root / "addons"), None, False)
    assert "Nothing to import" in pnetimport.run_import(r, Job(id="t", kind="pnet", label="x"), str(root / "addons"), None, False)

    remote = pnetimport.Source("root@10.0.0.5:/opt/unetlab/addons", r)
    assert remote.remote and remote.target == "root@10.0.0.5"
    with pytest.raises(pnetimport.PnetError):
        pnetimport.Source("-oProxyCommand=x@h:/p", r)


def test_docker_images(tmp_path):
    from netso import dockerimg
    from netso.jobs import Job
    r = Runner(force_dry_run=True)
    d = dockerimg.describe(r)
    frr = next(t for t in d["templates"] if t["template"] == "frr")
    assert frr["pullable"] and not frr["present"]
    assert not next(t for t in d["templates"] if t["template"] == "arista-ceos")["pullable"]
    assert dockerimg.normalize("alpine") == "alpine:latest" and dockerimg.normalize("quay.io/x/frr:1") == "quay.io/x/frr:1"
    assert "Pulled" in dockerimg.pull(r, Job(id="t", kind="docker", label="x"), "quay.io/frrouting/frr:10.2.1")
    assert r.log[-1] == "docker pull quay.io/frrouting/frr:10.2.1"
    with pytest.raises(dockerimg.DockerImageError):
        dockerimg.pull(r, Job(id="t", kind="docker", label="x"), "-rm; reboot")
    tar = tmp_path / "cEOS-lab.tar.xz"
    tar.write_bytes(b"x")
    assert "ceos:4.32" in dockerimg.load(r, Job(id="t", kind="docker", label="x"), tar, "ceos:4.32")
    assert r.log[-1].startswith("docker load -i")


def test_docker_images_api():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    assert any(t["template"] == "frr" for t in c.get("/api/images/docker").json()["templates"])
    assert c.post("/api/images/docker/pull", json={"ref": "alpine:3.20"}).status_code == 202
    assert c.post("/api/images/docker/pull", json={"ref": "bad ref"}).status_code == 400
    r = c.put("/api/images/docker-load?filename=crpd.tar&tag=crpd:23.4", content=b"tar")
    assert r.status_code == 202, r.text


def test_docker_missing_image_message():
    from netso.drivers.docker import DockerDriver
    from netso.hostcmd import HostError

    class R(Runner):
        def run(self, cmd, check=True, capture=True, fake=""):
            self.log.append(" ".join(cmd))
            if cmd[:2] == ["docker", "pull"]:
                raise HostError("pull access denied")
            return ""

    d = DockerDriver(R(force_dry_run=True))
    with pytest.raises(RuntimeError, match="vrnetlab"):
        d.ensure_image("vrnetlab/cisco_iol:17.12.01")
    with pytest.raises(RuntimeError, match="Load .tar"):
        d.ensure_image("ceos:latest")


def test_node_vnc_console():
    import socket
    import threading
    from fastapi.testclient import TestClient
    from netso import api as api_mod
    from netso.engine import load_state
    c = TestClient(api_mod.app)
    c.post("/api/labs", json={"yaml": "name: vnc1\nnodes:\n  - {name: pc, template: linux-vm}\n"})
    c.post("/api/labs/vnc1/deploy")
    ns = load_state("vnc1").nodes["pc"]
    assert ns.console_port is None and ns.vnc_display is not None
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 5900 + ns.vnc_display))
    srv.listen(1)

    def qemu_vnc():
        conn, _ = srv.accept()
        conn.sendall(b"RFB 003.008\n")
        conn.recv(1024)
        conn.close()

    threading.Thread(target=qemu_vnc, daemon=True).start()
    with c.websocket_connect("/api/labs/vnc1/nodes/pc/vnc", subprotocols=["binary"]) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        ws.send_bytes(b"x")
    srv.close()
    with pytest.raises(Exception):
        with c.websocket_connect("/api/labs/vnc1/nodes/nope/vnc") as ws:
            ws.receive_bytes()
    c.post("/api/labs/vnc1/destroy")


def test_iol_native_dry_run():
    from fastapi.testclient import TestClient
    from netso import api as api_mod
    from netso.engine import load_state
    t = topo.load_templates()
    assert t["cisco-iol"].kind == "iol" and t["cisco-iol"].interface_names()[:5] == ["e0/0", "e0/1", "e0/2", "e0/3", "e1/0"]
    assert len(t["cisco-iol-l2"].interface_names()) == 16
    c = TestClient(api_mod.app)
    lab = ("name: iol1\nnodes:\n  - {name: r1, template: cisco-iol}\n  - {name: sw1, template: cisco-iol-l2}\n"
           "links:\n  - endpoints: ['r1:e0/0', 'sw1:e1/2']\n")
    assert c.post("/api/labs", json={"yaml": lab}).status_code in (200, 201)
    c.post("/api/labs/iol1/deploy")
    st = load_state("iol1").nodes
    assert st["r1"].status == "running" and st["r1"].console_port and st["r1"].iol_id != st["sw1"].iol_id
    log = "\n".join(api_mod.engine.r.log)
    assert "netso.iolwrap" in log and "--tap 0/0=" in log and "--eth 2" in log and "--eth 4" in log
    assert api_mod.engine.console_command("iol1", "r1")[0] == "telnet"
    ids = (st["r1"].iol_id, st["sw1"].iol_id)
    c.post("/api/labs/iol1/destroy")
    c.post("/api/labs/iol1/deploy")  # same numbers again (the NVRAM file is named after them)
    st = load_state("iol1").nodes
    assert (st["r1"].iol_id, st["sw1"].iol_id) == ids
    c.post("/api/labs/iol1/destroy")
    # license: the user's own file only
    assert c.get("/api/images/iol-license").json()["present"] is False
    assert c.put("/api/images/iol-license", json={"text": "chiave a caso"}).status_code == 400
    lic = c.put("/api/images/iol-license", json={"text": "[license]\nnetso = 0123456789abcdef;"}).json()
    assert lic["present"] and lic["text"].startswith("[license]")
    assert c.put("/api/images/iol-license", json={"text": ""}).json()["present"] is False


def test_iolwrap_console_and_frames(tmp_path):
    import socket
    import struct
    import subprocess
    import sys
    import time
    from netso import iolwrap
    assert iolwrap.Telnet().feed(bytes([255, 251, 1]) + b"ab\r\x00c\r\n" + bytes([255, 250, 24, 0, 255, 240])) == b"ab\rc\r"
    assert iolwrap.netmap(7, [(0, 0), (1, 2)]) == "7:0/0 519:0/0\n7:1/2 519:1/1\n"
    assert iolwrap.port_byte(1, 2) == 0x21
    # a fake IOL: prints a banner, echoes what it gets, and answers on its netio socket like an instance would
    fake = tmp_path / "fake-iol"
    fake.write_text(f"""#!{sys.executable}
import os, socket, sys
me = "/tmp/netio%d/%s" % (os.getuid(), sys.argv[-1])
os.unlink(me) if os.path.exists(me) else None
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); s.bind(me)
open("args.txt", "w").write(" ".join(sys.argv[1:]))
os.write(1, b"IOL pronto\\r\\n")
while True:
    c = os.read(0, 1)
    if not c or c == b"q":
        break
    os.write(1, c.upper())
s.sendto(bytes([0x02, 0x0b, 0, 9, 0x00, 0x10, 1, 0]) + b"frame", "/tmp/netio%d/%d" % (os.getuid(), 9 + 512))
""")
    fake.chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    import os
    netio = f"/tmp/netio{os.getuid()}"
    os.makedirs(netio, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)  # a socket + lock left by a crashed IOL id 9 must not block the new one
    try:
        stale.bind(f"{netio}/9")
    except OSError:
        os.unlink(f"{netio}/9"); stale.bind(f"{netio}/9")
    open(f"{netio}/9.lck", "w").close()
    p = subprocess.Popen([sys.executable, "-m", "netso.iolwrap", "--dir", str(tmp_path), "--bin", str(fake), "--id", "9",
                          "--port", str(port), "--eth", "1"])
    stale.close()
    try:
        for _ in range(50):
            try:
                cli = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                time.sleep(0.1)
        buf = b""
        while b"IOL pronto" not in buf:
            buf += cli.recv(1024)
        cli.sendall(bytes([255, 253, 1]) + b"ok")
        while b"OK" not in buf:
            buf += cli.recv(1024)
        assert (tmp_path / "NETMAP").read_text() == ""
        assert (tmp_path / "args.txt").read_text() == "-e 1 -s 0 -m 512 -n 1024 9"
        assert not os.path.exists(f"{netio}/9.lck")  # the stale lock was cleared, not left to block IOL
        cli.sendall(b"q")
        assert p.wait(timeout=5) == 1  # IOL ended -> the wrapper ends too
        assert b"IOL pronto" in (tmp_path / "iol-exit.log").read_bytes()
        cli.close()
    finally:
        p.kill()


def test_guest_consoles_rdp_http_ssh(monkeypatch):
    import socket
    import threading
    from fastapi.testclient import TestClient
    from netso import api as api_mod, guac
    from netso.engine import load_state
    assert guac.encode("select", "rdp") == "6.select,3.rdp;"
    parsed, raw, rest = guac.split("4.size,4.1024,3.768;5.mouse,1.")
    assert parsed == [["size", "1024", "768"]] and raw == ["4.size,4.1024,3.768;"] and rest == "5.mouse,1."
    assert guac.rdp_params("rdp-tls", "10.0.0.5")["security"] == "tls" and guac.rdp_params("rdp", "1.2.3.4", "admin")["security"] == "any"

    c = TestClient(api_mod.app)
    lab = ("name: gst1\nnodes:\n  - {name: win, template: linux-vm, console: rdp, mgmt_ip: 127.0.0.1}\n"
           "  - {name: web, template: linux-vm, console: http, mgmt_ip: 127.0.0.1}\n"
           "  - {name: sp, template: linux-vm, console: spice}\n  - {name: sh, template: linux-vm, console: ssh, mgmt_ip: 10.1.1.1}\n"
           "  - {name: noip, template: linux-vm, console: https}\n")
    assert c.post("/api/labs", json={"yaml": lab}).status_code == 201
    c.post("/api/labs/gst1/deploy")
    st = load_state("gst1").nodes
    assert st["win"].console == "rdp" and st["sp"].spice_port and not st["win"].console_port
    assert "-spice" in next(x for x in api_mod.engine.r.log if "-name gst1-sp " in x)
    sp_cmd = next(x for x in api_mod.engine.r.log if "-name gst1-sp " in x)
    assert "spicevmc,id=vdagent" in sp_cmd and "com.redhat.spice.0" in sp_cmd  # shared clipboard via SPICE agent
    assert "vdagent" not in next(x for x in api_mod.engine.r.log if "-name gst1-win " in x)  # RDP: clipboard via guacd
    assert c.post("/api/labs/gst1/nodes/sp/open", json={}).json() == {"type": "spice", "port": st["sp"].spice_port}
    assert "ssh" in " ".join(api_mod.engine.console_command("gst1", "sh")) and "10.1.1.1" in api_mod.engine.console_command("gst1", "sh")
    r = c.post("/api/labs/gst1/nodes/noip/open", json={})
    assert r.status_code == 400 and "management IP" in r.json()["detail"]

    # HTTP: a port of the server forwarded to the guest's web server
    web = socket.socket()
    web.bind(("127.0.0.1", 0))
    web.listen(1)
    monkeypatch.setitem(api_mod.GUEST_PORTS, "http", web.getsockname()[1])

    fwd = c.post("/api/labs/gst1/nodes/web/open", json={}).json()
    assert fwd["type"] == "http" and fwd["port"]
    # (the forward lives in the app's event loop; reaching it from here is covered on the real server)

    # RDP: ticket, then the Guacamole tunnel through a fake guacd
    fake = socket.socket()
    fake.bind(("127.0.0.1", 0))
    fake.listen(1)
    monkeypatch.setattr(guac, "GUACD_PORT", fake.getsockname()[1])
    got = {}

    def guacd():
        conn, _ = fake.accept()
        assert conn.recv(100) == b"6.select,3.rdp;"
        conn.sendall(b"4.args,13.VERSION_1_5_0,8.hostname,4.port,8.security;")
        data = b""
        while b"7.connect" not in data or not data.endswith(b";"):
            data += conn.recv(4096)
        got["hs"] = data.decode()
        conn.sendall(b"5.ready,4.$abc;4.sync,")  # an instruction split across two reads
        conn.sendall(b"3.123;")
        got["key"] = conn.recv(100)
        conn.close()

    threading.Thread(target=guacd, daemon=True).start()
    t = c.post("/api/labs/gst1/nodes/win/open", json={"width": 800, "height": 600}).json()
    assert t["type"] == "rdp" and t["ticket"]
    with c.websocket_connect(f"/api/labs/gst1/nodes/win/guac?ticket={t['ticket']}", subprotocols=["guacamole"]) as ws:
        first = ws.receive_text()
        assert first.startswith("0.,36.")
        seen = ""
        while "4.sync,3.123;" not in seen:
            seen += ws.receive_text()
        assert "5.ready" in seen
        ws.send_text("0.,4.ping,13.1700000000000;")
        assert ws.receive_text() == "0.,4.ping,13.1700000000000;"  # answered by NetSO, not guacd
        ws.send_text("3.key,5.65307,1.1;")
    assert "4.size,3.800,3.600" in got["hs"] and "7.connect,13.VERSION_1_5_0,9.127.0.0.1,4.3389,3.rdp;" in got["hs"]
    assert got["key"] == b"3.key,5.65307,1.1;"
    with pytest.raises(Exception):  # a ticket works once
        with c.websocket_connect(f"/api/labs/gst1/nodes/win/guac?ticket={t['ticket']}") as ws:
            ws.receive_text()
    c.post("/api/labs/gst1/destroy")
    web.close()
    fake.close()


def test_guest_port_forward():
    import asyncio
    from netso import guac

    async def run():
        async def web(r, w):
            await r.read(100)
            w.write(b"HTTP/1.0 200 OK\r\n\r\nciao")
            await w.drain()
            w.close()

        srv = await asyncio.start_server(web, "127.0.0.1", 0)
        port = await guac.forward("127.0.0.1", srv.sockets[0].getsockname()[1])
        assert port == await guac.forward("127.0.0.1", srv.sockets[0].getsockname()[1])  # reused
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"GET / HTTP/1.0\r\n\r\n")
        await w.drain()
        assert (await r.read()).endswith(b"ciao")
        srv.close()

    asyncio.run(run())
    guac._forwards.clear()


def test_image_choices_by_type():
    from fastapi.testclient import TestClient
    from netso import api as api_mod, catalog
    from netso import images as imgs
    from netso.models import Template
    for d in ("firepower6-FMC-7.2.0-82", "firepower6-FTD-7.3.1-19", "vios-15.6"):
        (settings.images / d).mkdir(parents=True, exist_ok=True)
        (settings.images / d / "hda.qcow2").write_bytes(b"x")
    catalog.save_template(Template(name="firepower6", title="Cisco FirePower 6", kind="qemu", image="firepower6-FTD-7.3.1-19"))
    folders = imgs.folders_with_disks()
    t = topo.load_templates()["firepower6"]
    assert imgs.choices(t, folders, []) == ["firepower6-FMC-7.2.0-82", "firepower6-FTD-7.3.1-19"]
    rows = {x["name"]: x for x in TestClient(api_mod.app).get("/api/templates").json()}
    assert rows["firepower6"]["ready"] and rows["firepower6"]["title"] == "Cisco FirePower 6"
    assert rows["cisco-c8000v"]["ready"] is False and rows["cisco-c8000v"]["choices"] == []
    assert rows["docker"]["kind"] == "docker" and rows["bridge"]["ready"]
    catalog.user_file("firepower6").unlink()


def test_vnc_clipboard_agent():
    from fastapi.testclient import TestClient
    from netso import api as api_mod
    c = TestClient(api_mod.app)
    c.post("/api/labs", json={"yaml": "name: clip1\nnodes:\n  - {name: pc, template: linux-vm}\n  - {name: r1, template: cisco-iosv}\n"})
    c.post("/api/labs/clip1/deploy")
    pc = next(x for x in api_mod.engine.r.log if "-name clip1-pc " in x)
    r1 = next(x for x in api_mod.engine.r.log if "-name clip1-r1 " in x)
    assert "qemu-vdagent,id=vdagent,name=vdagent,clipboard=on" in pc and "virtserialport" in pc
    assert "vdagent" not in r1  # serial console: no agent channel
    c.post("/api/labs/clip1/destroy")


def test_iol_binary_lookup():
    from netso.drivers.iol import iol_binary
    for folder, f in [("iol-L3-ADVENTERPRISEK9-M-15.4-2T", "L3-ADVENTERPRISEK9-M-15.4-2T.bin"),
                      ("iol-i86bi_linux_l2-adventerprisek9-ms.SSA.high_iron_20190423", "i86bi_linux_l2-adventerprisek9-ms.SSA.high_iron_20190423.bin")]:
        (settings.images / folder).mkdir(parents=True, exist_ok=True)
        (settings.images / folder / f).write_bytes(b"\x7fELF")
    assert iol_binary("iol-l3", False).name.startswith("L3-")  # default name missing: an L3 image
    assert "l2" in iol_binary("iol-l2", True).name  # switch: an L2 image
    assert iol_binary("iol-L3-ADVENTERPRISEK9-M-15.4-2T", True).name.startswith("L3-")  # the chosen one wins
    from netso import images as imgs
    (settings.images / "iol-noext").mkdir(exist_ok=True)
    (settings.images / "iol-noext" / "i86bi-linux").write_bytes(b"\x7fELF..")  # no .bin: still an IOL binary
    (settings.images / "iol-half").mkdir(exist_ok=True)
    (settings.images / "iol-half" / "x.bin.part").write_bytes(b"\x7fELF")
    assert "iol-noext" in imgs.folders_with_disks() and "iol-half" not in imgs.folders_with_disks()
    assert imgs.iol_problems() == ["iol-half: unfinished copy (.part), re-run the import from PNETLab"]


def test_custom_icons_module():
    import pytest as _pytest
    from netso import icons

    assert icons.listing() == []
    r = icons.save("acme-router", "router.svg", b"<svg><circle r='3'/></svg>")
    assert r == {"name": "acme-router", "ext": "svg"}
    names = [i["name"] for i in icons.listing()]
    assert names == ["acme-router"]
    assert icons.path("acme-router").read_bytes().startswith(b"<svg")
    # reusing the name with another extension replaces the file (one per name)
    icons.save("acme-router", "router.png", b"\x89PNG\r\n")
    assert icons.path("acme-router").suffix == ".png" and icons.media_type(icons.path("acme-router")) == "image/png"
    with _pytest.raises(icons.IconError):
        icons.save("bad name/..", "x.svg", b"<svg/>")
    with _pytest.raises(icons.IconError):
        icons.save("exe", "x.exe", b"MZ")
    with _pytest.raises(icons.IconError):
        icons.save("big", "x.png", b"x" * (icons.MAX_BYTES + 1))
    with _pytest.raises(icons.IconError):
        icons.save("evil", "x.svg", b"<svg><script>alert(1)</script></svg>")
    icons.delete("acme-router")
    assert icons.listing() == []
    with _pytest.raises(FileNotFoundError):
        icons.path("acme-router")


def test_custom_icons_api():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    assert c.get("/api/icons").json() == []
    r = c.put("/api/icons/mysw/upload?filename=sw.svg", content=b"<svg><rect/></svg>")
    assert r.status_code == 200 and r.json()["name"] == "mysw"
    assert [i["name"] for i in c.get("/api/icons").json()] == ["mysw"]
    img = c.get("/api/icons/mysw")
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/svg")
    assert c.put("/api/icons/bad/upload?filename=x.exe", content=b"MZ").status_code == 400
    assert c.get("/api/icons/nope").status_code == 404
    assert c.delete("/api/icons/mysw").status_code == 200
    assert c.get("/api/icons").json() == []


def test_generate_lab_api():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    r = c.post("/api/labs/generate", json={"name": "clos1", "pattern": "clos",
               "params": {"spines": 2, "leaves": 2, "hosts": 1, "spine_tpl": "frr", "leaf_tpl": "frr", "host_tpl": "vpc"}})
    assert r.status_code == 201
    y = c.get("/api/labs/clos1/yaml").text
    assert "spine1" in y and "leaf1" in y
    summary = {lb["name"]: lb for lb in c.get("/api/labs").json()}
    assert summary["clos1"]["nodes"] == 6 and summary["clos1"]["links"] > 0  # 2 spine + 2 leaf + 2 host
    # a plain ring too
    assert c.post("/api/labs/generate", json={"name": "ring1", "pattern": "ring", "params": {"count": 4, "tpl": "frr"}}).status_code == 201
    assert {lb["name"]: lb for lb in c.get("/api/labs").json()}["ring1"]["links"] == 4
    # duplicate name and unknown pattern are refused
    assert c.post("/api/labs/generate", json={"name": "clos1", "pattern": "ring", "params": {"count": 3, "tpl": "frr"}}).status_code == 409
    assert c.post("/api/labs/generate", json={"name": "zz", "pattern": "nope", "params": {}}).status_code == 400


def test_scenarios_build_all():
    from netso import scenarios, topology as topo
    tpls = topo.load_templates()
    for t in scenarios.TOPICS:
        spec = scenarios.build(t["id"], tpls, seed=7)
        assert spec["nodes"] and "description" in spec and spec["instructions"]
        if t["id"] not in ("vlan", "mitm-arp", "recon-scan"):   # these are host-based (no FRR config)
            assert spec["configs"] and all("frr version" in c for c in spec["configs"].values())
            # nodes with a config boot from it
            named = {n["name"] for n in spec["nodes"] if n.get("startup_config")}
            assert named == set(spec["configs"])


def test_scenarios_multivendor_ios():
    from netso import scenarios, topology as topo
    tpls = topo.load_templates()
    # dialect detection: FRR, Cisco IOS families, and "not a router"
    assert scenarios.dialect_of(tpls["frr"]) == "frr"
    assert scenarios.dialect_of(tpls["cisco-iosv"]) == "ios"
    assert scenarios.dialect_of(tpls["cisco-iol"]) == "ios"
    assert scenarios.dialect_of(tpls["bridge"]) is None
    assert scenarios.dialect_of(tpls["cisco-iol-l2"]) is None   # L2 switch, not a router
    # core topics generate real Cisco IOS config on a Cisco router
    for topic in ("ospf", "bgp-ebgp", "eigrp", "isis", "static", "rip"):
        spec = scenarios.build(topic, tpls, seed=3, count=3, router="cisco-iosv")
        assert all(n["template"] == "cisco-iosv" for n in spec["nodes"])
        sol = next(iter(spec["solution"].values()))
        assert "interface Loopback0" in sol and "255.255.255.252" in sol  # IOS addressing
        assert "frr version" not in sol                                   # not FRR syntax
    # advanced topics now generate Cisco IOS too (MPLS L3VPN: VRF + MP-BGP VPNv4)
    spec = scenarios.build("mpls-l3vpn", tpls, seed=1, count=3, router="cisco-iosv")
    pe_cfg = "".join(spec["solution"].values())
    assert "vrf definition CUST" in pe_cfg and "address-family vpnv4" in pe_cfg and "mpls ip" in pe_cfg
    assert "frr version" not in pe_cfg
    # VXLAN/EVPN stays FRR-only (not reliable on IOL/IOSv) and is refused with a clear message
    with pytest.raises(Exception) as e:
        scenarios.build("vxlan-evpn", tpls, seed=1, count=3, router="cisco-iosv")
    assert "FRR" in str(e.value)
    # no router given -> defaults to FRR and still works
    assert scenarios.build("ospf", tpls, seed=1, count=3)["nodes"]


def test_generate_scenario_api():
    from fastapi.testclient import TestClient
    from netso import topology as topo
    from netso.api import app
    c = TestClient(app)
    r = c.post("/api/labs/generate", json={"name": "ospf-lab", "scenario": "ospf", "count": 5})
    assert r.status_code == 201
    lab = topo.load_lab("ospf-lab")
    assert len(lab.nodes) == 5 and all(n.template == "frr" for n in lab.nodes)   # count honoured
    assert any(n.startup_config for n in lab.nodes)
    assert lab.instructions and "Soluzione" in lab.instructions                  # guide included
    assert any(ln.label and "/30" in ln.label for ln in lab.links)              # IPs on the links
    base = topo.read_configs("ospf-lab", "default")          # boots with IP addressing only…
    assert base and all("ip address" in v and "router ospf" not in v for v in base.values())
    sol = topo.read_configs("ospf-lab", "soluzione")         # …the protocol lives in the «soluzione» set
    assert sol and all("router ospf" in v for v in sol.values())
    # unknown scenario -> 400
    assert c.post("/api/labs/generate", json={"name": "zz", "scenario": "nope"}).status_code == 400
    # scenario list endpoint
    ids = {s["id"] for s in c.get("/api/scenarios").json()}
    assert {"ospf", "bgp-ebgp", "isis", "vxlan-evpn"} <= ids


def test_agents_crud_and_request():
    from netso import agents
    assert agents.listing() == []
    a = agents.save({"name": "Claude", "kind": "anthropic", "api_key": "sk-test", "model": "claude-sonnet-4-5"})
    assert a["has_key"] is True and "api_key" not in a   # key never returned
    assert agents.listing()[0]["name"] == "Claude"
    # editing without a key keeps the stored one
    agents.save({"id": a["id"], "name": "Claude 2", "kind": "anthropic"})
    stored = agents._get(a["id"])
    assert stored["api_key"] == "sk-test" and stored["name"] == "Claude 2"
    # request building per provider
    url, h, b = agents.build_request(stored, "ciao", system="sys")
    assert url.endswith("/v1/messages") and h["x-api-key"] == "sk-test" and b["messages"][0]["content"] == "ciao"
    assert agents.parse_response("anthropic", {"content": [{"type": "text", "text": "ok"}]}) == "ok"
    oai = agents.save({"name": "GPT", "kind": "openai", "api_key": "k", "base_url": "https://api.openai.com"})
    u2, h2, b2 = agents.build_request(agents._get(oai["id"]), "hi")
    assert u2.endswith("/v1/chat/completions") and h2["authorization"] == "Bearer k"
    assert agents.parse_response("openai", {"choices": [{"message": {"content": "hey"}}]}) == "hey"
    # a provider that needs a key but has none
    import pytest as _pytest
    g = agents.save({"name": "Gem", "kind": "google"})
    with _pytest.raises(agents.AgentError):
        agents.build_request(agents._get(g["id"]), "x")
    agents.delete(a["id"])
    assert all(x["id"] != a["id"] for x in agents.listing())


def test_agents_api():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    r = c.get("/api/agents").json()
    assert r["agents"] == [] and any(k["id"] == "anthropic" for k in r["kinds"])
    saved = c.post("/api/agents", json={"name": "Local", "kind": "ollama", "model": "llama3.1"}).json()
    assert saved["kind"] == "ollama" and saved["has_key"] is False
    assert c.delete(f"/api/agents/{saved['id']}").status_code == 200


def test_agents_cli_subscription():
    from netso import agents
    a = agents.save({"name": "Claude Pro", "kind": "cli", "command": "cat"})
    assert a["auth"] == "cli" and a["command"] == "cat" and a["has_key"] is False
    # 'cat' echoes stdin: the prompt (with system prepended) comes straight back
    out = agents.ask(a["id"], "ciao mondo", system="SYS")
    assert "ciao mondo" in out and "SYS" in out
    # {prompt} placeholder passes it as an argument instead of stdin
    b = agents.save({"name": "echo", "kind": "cli", "command": "printf %s {prompt}"})
    assert agents.ask(b["id"], "ping") == "ping"
    # cli needs a command
    import pytest as _pytest
    with _pytest.raises(agents.AgentError):
        agents.save({"name": "bad", "kind": "cli", "command": ""})
    # a missing binary is reported, not crashed
    m = agents.save({"name": "nope", "kind": "cli", "command": "definitely-not-a-real-cmd-xyz"})
    with _pytest.raises(agents.AgentError):
        agents.ask(m["id"], "x")


def test_assistant_lab_context():
    from fastapi.testclient import TestClient
    from netso import agents
    from netso.api import app
    c = TestClient(app)
    assert c.post("/api/labs/generate", json={"name": "mpls-lab", "scenario": "mpls-ldp"}).status_code == 201
    a = agents.save({"name": "echo", "kind": "cli", "command": "cat"})  # echoes the full prompt back
    r = c.post(f"/api/agents/{a['id']}/ask", json={"prompt": "LDP non si forma, aiuto", "lab": "mpls-lab", "node": "r1"})
    assert r.status_code == 200
    txt = r.json()["text"]
    # the assistant received the whole lab: topology, links and the applied (base) config
    assert "Contesto del lab" in txt and "Collegamenti" in txt
    assert "ip address" in txt and "interface eth" in txt        # the base config (IP addressing) is applied
    assert "bloccato soprattutto sul nodo «r1»" in txt           # node focus
    assert "LDP non si forma" in txt                             # the user's question


def test_diagnostics_readonly_filter():
    from netso import diagnostics as dg
    ok = ["show ip ospf neighbor", "show mpls ldp binding", "ping -c 1 10.0.0.2", "traceroute 10.0.0.2",
          "ip route", "ip -br addr", "cat /etc/frr/frr.conf"]
    bad = ["conf t", "reboot", "ip route add 0.0.0.0/0 via 1.1.1.1", "ip link set eth1 down",
           "show run; rm -rf /", "show run | include x", "cat /etc/passwd > /tmp/x", "vtysh -c 'conf t'"]
    assert all(dg.is_readonly(c) for c in ok)
    assert not any(dg.is_readonly(c) for c in bad)


def test_diagnostics_run_node_off(tmp_path):
    from fastapi.testclient import TestClient
    from netso import diagnostics as dg
    from netso.api import app
    TestClient(app).post("/api/labs/generate", json={"name": "diag-lab", "scenario": "ospf"})
    import pytest as _pytest
    with _pytest.raises(dg.DiagError):           # nodes are stopped in a dry run
        dg.run("diag-lab", "r1", "show ip ospf neighbor")
    with _pytest.raises(dg.DiagError):           # read-only filter still applies
        dg.run("diag-lab", "r1", "reboot")


def test_assistant_live_loop():
    from fastapi.testclient import TestClient
    from netso import agents
    from netso.api import app
    c = TestClient(app)
    c.post("/api/labs/generate", json={"name": "live-lab", "scenario": "ospf"})
    a = agents.save({"name": "echo", "kind": "cli", "command": "cat"})  # echoes RUN directives back
    r = c.post(f"/api/agents/{a['id']}/ask",
               json={"prompt": "Controlla le adiacenze.\nRUN r1: show ip ospf neighbor", "lab": "live-lab", "live": True})
    assert r.status_code == 200
    body = r.json()
    # the loop parsed the directive, executed it (node off → reported error) and recorded a transcript
    assert body["transcript"] and body["transcript"][0]["node"] == "r1"
    assert body["transcript"][0]["cmd"] == "show ip ospf neighbor"
    assert "powered off" in body["transcript"][0]["output"] or "ERROR" in body["transcript"][0]["output"]


def test_assistant_chat_history():
    from fastapi.testclient import TestClient
    from netso import agents
    from netso.api import app
    c = TestClient(app)
    a = agents.save({"name": "echo", "kind": "cli", "command": "cat"})
    r = c.post(f"/api/agents/{a['id']}/ask", json={"prompt": "e il secondo?",
               "history": [{"role": "user", "text": "primo messaggio"}, {"role": "assistant", "text": "prima risposta"}]})
    txt = r.json()["text"]
    assert "Conversazione finora" in txt and "primo messaggio" in txt and "prima risposta" in txt
    assert "Nuovo messaggio dell'utente" in txt and "e il secondo?" in txt


def test_status_api():
    from fastapi.testclient import TestClient
    from netso.api import app
    c = TestClient(app)
    c.post("/api/labs", json={"yaml": EXAMPLE})
    s = c.get("/api/status").json()
    assert "host" in s and "labs" in s and s["labs"]["total"] >= 1
    assert "disks" in s and ("home" in s["disks"])
    # keys present even if some live values are None off-Linux
    assert set(["version", "memory", "load", "uptime_seconds"]) <= set(s)


def test_lab_name_with_spaces():
    from fastapi.testclient import TestClient
    from netso import topology as topo
    from netso.api import app
    c = TestClient(app)
    assert c.post("/api/labs", json={"name": "OSPF lab 1", "description": "x"}).status_code == 201
    assert "OSPF lab 1" in topo.list_labs()
    assert c.get("/api/labs/OSPF%20lab%201/yaml").status_code == 200
    # trailing/leading spaces trimmed; '/' and double spaces rejected
    import pytest as _pytest
    from netso.models import Lab
    assert Lab(name="  Spazi ok  ").name == "Spazi ok"
    for bad in ("a/b", "a  b", "1lab"):
        with _pytest.raises(Exception):
            Lab(name=bad)


def test_stop_lab_and_status(monkeypatch):
    from fastapi.testclient import TestClient
    from netso import engine as eng
    from netso.api import app
    c = TestClient(app)
    c.post("/api/labs", json={"yaml": EXAMPLE})
    c.post("/api/labs/t1/deploy")
    st = c.get("/api/labs").json()
    row = next(x for x in st if x["name"] == "t1")
    assert row["status"] == "running" and row["running"] > 0   # deployed: avviato
    # stop the whole lab -> fermo, no nodes running
    r = c.post("/api/labs/t1/stop")
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    row = next(x for x in c.get("/api/labs").json() if x["name"] == "t1")
    assert row["status"] == "stopped" and row["running"] == 0
    # refresh derives the lab status from the nodes (no node running -> stopped)
    s = eng.load_state("t1")
    assert all(n.status != "running" for n in s.nodes.values())
