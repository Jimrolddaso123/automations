import re

from flask import Flask, render_template, request
from netmiko import ConnectHandler

app = Flask(__name__)

# Single place to edit the router details (shown on the page too)
ROUTER_DEVICE = {
    'device_type': 'cisco_ios_telnet',
    'ip': '192.168.10.71',
    'port': 32770,
    'username': 'admin',
    'password': 'eve123',
    'secret': 'eve123',
    # EVE-NG consoles are slow to answer, so give Netmiko more time
    'global_delay_factor': 2,
    'conn_timeout': 30,
    'auth_timeout': 30,
    'banner_timeout': 30,
}

COMMANDS_TO_RUN = [
    "show version",
    "show ip interface brief",
    "show running-config",
]

IFACE_RE = re.compile(
    r'^(\S+)\s+(\S+)\s+(?:YES|NO)\s+\S+\s+(administratively down|up|down)\s+(up|down)\s*$',
    re.IGNORECASE,
)


def parse_interfaces(text):
    """Turn 'show ip interface brief' into a list of dicts."""
    rows = []
    for line in text.splitlines():
        m = IFACE_RE.match(line.strip())
        if not m:
            continue
        name, ip, status, proto = m.groups()
        status, proto = status.lower(), proto.lower()
        if status == "administratively down":
            state = "admin"
        elif status == "up" and proto == "up":
            state = "up"
        else:
            state = "down"
        rows.append({"name": name, "ip": ip, "status": status,
                     "proto": proto, "state": state})
    return rows


def pick(pattern, text, default="-"):
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


def build_dashboard(results):
    """Summarise the raw command output for the dashboard cards."""
    version = results.get("show version", "")
    ifaces = parse_interfaces(results.get("show ip interface brief", ""))
    total = len(ifaces)

    counts = {"up": 0, "down": 0, "admin": 0}
    for i in ifaces:
        counts[i["state"]] += 1
    assigned = sum(1 for i in ifaces if i["ip"].lower() != "unassigned")

    def pct(n):
        return round(n * 100 / total) if total else 0

    # Donut segments (circumference is normalised to 100)
    segments, offset = [], 0
    for key, label, color in (("up", "Up", "#7a5cff"),
                              ("down", "Down", "#ff6a4d"),
                              ("admin", "Admin down", "#5bb5c4")):
        share = counts[key] * 100 / total if total else 0
        if share:
            segments.append({"label": label, "count": counts[key], "color": color,
                             "dash": max(share - 1.2, 0.5), "offset": -offset})
        offset += share

    line_counts = {cmd: len(out.splitlines()) for cmd, out in results.items()}
    biggest = max(line_counts.values()) if line_counts else 1

    return {
        "interfaces": ifaces,
        "total": total,
        "counts": counts,
        "up_pct": pct(counts["up"]),
        "assigned_pct": pct(assigned),
        "gauge_offset": 100 - pct(counts["up"]),
        "segments": segments,
        "total_lines": sum(line_counts.values()),
        "bars": [{"cmd": c, "lines": n, "h": max(round(n * 100 / biggest), 12)}
                 for c, n in line_counts.items()],
        "hostname": pick(r'^hostname\s+(\S+)', results.get("show running-config", ""),
                         pick(r'^(\S+) uptime is', version)),
        "ios": pick(r'Version ([^\s,]+)', version),
        "uptime": pick(r'uptime is (.+)', version),
        "model": pick(r'^[Cc]isco (\S+) .*processor', version),
    }


def explain(err):
    """Turn a Netmiko error into a plain-language next step."""
    text = str(err).lower()
    if "login failed" in text or "pattern not detected" in text:
        return ("The router console did not accept the login. Make sure no other console "
                "window is open on R1 (EVE-NG browser console, PuTTY, a second Python run), "
                "wait 10 seconds, then try again. If it keeps failing, telnet to the port "
                "by hand to see what prompt R1 shows.")
    if "timed out" in text or "timeout" in text:
        return "R1 did not answer. Check that the node is started in EVE-NG and the port is correct."
    if "refused" in text or "unreachable" in text:
        return "Nothing is listening at that address and port. Check the EVE-NG IP and the node's console port."
    return "Check that the lab is running, the port matches the node, and the router accepts Telnet."


@app.route('/', methods=['GET', 'POST'])
def index():
    results = {}
    error = None
    hint = None
    data = None

    if request.method == 'POST':
        net_connect = None
        try:
            net_connect = ConnectHandler(**ROUTER_DEVICE)
            net_connect.enable()

            for cmd in COMMANDS_TO_RUN:
                results[cmd] = net_connect.send_command(cmd, read_timeout=30)

            data = build_dashboard(results)

        except Exception as e:
            error = f"Connection failed: {e}"
            hint = explain(e)
            results = {}

        finally:
            # Always release the console, otherwise the next login can fail
            if net_connect is not None:
                try:
                    net_connect.disconnect()
                except Exception:
                    pass

    return render_template(
        "index.html",
        results=results,
        error=error,
        hint=hint,
        data=data,
        device=ROUTER_DEVICE,
    )


if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)