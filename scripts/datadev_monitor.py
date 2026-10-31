#!/sdf/group/lcls/ds/ana/sw/conda1/inst/envs/ana_20241215/bin/python
"""
datadev Node Status Monitor
Checks datadev-related files on configurable node sets via SSH.

Node sets available (select via radio buttons):
  - drp-srcf-cmp 001..080
  - drp-neh-cmp  001..030
  - drp-srcf-mon 001..030

Checks per node:
  1. /proc/datadev_*          - kernel module loaded?
  2. /usr/local/sbin/datadev.ko  - module file present?
  3. /etc/systemd/system/datadev.service  - service file present?
  4. datadev.service contains cfgRxCount?
  5. /usr/share/hwdata/pci.ids - PCI ID database present?
  6. Firmware image name  - from the 'Build String' in /proc/datadev_*
  7. Firmware version     - from 'Firmware Version' in /proc/datadev_*
  8. ExecStart line from datadev.service

The firmware columns are read from every /proc/datadev_N on the node. When the
cards on one node do not all run the same image/version the cell shows every
value separated by ' / ' and is flagged amber. Hover for the full build stamp
(Vivado version, build host, build date, builder), the firmware git hash and
the DMA driver's git version.

CSV export:
  - 'Export CSV…' in the toolbar saves the last refresh, including the build
    detail that the GUI only shows in tooltips.
  - Headless (no X / no Qt display needed), for cron or a quick diff:
        ./datadev_monitor.py --csv out.csv [--node-set drp-neh-cmp]
        ./datadev_monitor.py --csv -            # write to stdout
"""

import re
import os
import csv
import sys
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QStatusBar,
    QProgressBar,
    QSizePolicy,
    QButtonGroup,
    QRadioButton,
    QGroupBox,
    QFileDialog,
    QMessageBox,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt5.QtGui import QBrush, QColor, QFont

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NODE_SETS = {
    "drp-srcf-cmp (01-80)": {"prefix": "drp-srcf-cmp", "range": range(1, 81)},
    "drp-neh-cmp (01-30)": {"prefix": "drp-neh-cmp", "range": range(1, 31)},
    "drp-srcf-mon (01-30)": {"prefix": "drp-srcf-mon", "range": range(1, 31)},
}
DEFAULT_NODE_SET = "drp-srcf-cmp (01-80)"

MAX_WORKERS = 20  # parallel SSH connections
SSH_TIMEOUT = 25  # seconds total per node (connect + script execution)
SSH_CONNECT_TIMEOUT = 4  # seconds for TCP/SSH handshake only

# Remote bash snippet – all checks in one SSH round-trip
CHECK_SCRIPT = r"""
# 1) /proc/datadev_*
proc_list=$(ls /proc/datadev_* 2>/dev/null | tr '\n' ',' | sed 's/,$//')
if [ -n "$proc_list" ]; then
    echo "PROC:${proc_list}"
else
    echo "PROC:NONE"
fi

# 2) /usr/local/sbin/datadev.ko
if [ -f /usr/local/sbin/datadev.ko ]; then echo "KO:YES"; else echo "KO:NO"; fi

# 3-5) datadev.service
SVC=/etc/systemd/system/datadev.service
if [ -f "$SVC" ]; then
    echo "SVC:YES"
    if grep -q cfgRxCount "$SVC" 2>/dev/null; then echo "CFG:YES"; else echo "CFG:NO"; fi
    execline=$(grep -m1 '^ExecStart' "$SVC" 2>/dev/null)
    echo "EXEC:${execline}"
else
    echo "SVC:NO"
    echo "CFG:N/A"
    echo "EXEC:N/A"
fi

# 6) /usr/share/hwdata/pci.ids
if [ -f /usr/share/hwdata/pci.ids ]; then echo "PCIIDS:YES"; else echo "PCIIDS:NO"; fi

# 7) Firmware info from each /proc/datadev_N
#    Emits one line per card:
#      FW:<dev>|<fwversion>|<githash>|<buildstring>|<driver git version>
#    awk quits right after the driver version line so we never read the
#    (very long) per-buffer dump that follows it.
for p in /proc/datadev_*; do
    [ -e "$p" ] || continue
    awk -v dev="${p#/proc/datadev_}" '
        function val(s) {
            i = index(s, " : ")
            if (i == 0) return ""
            s = substr(s, i + 3)
            gsub(/^[ \t]+|[ \t\r]+$/, "", s)
            return s
        }
        /^[ \t]*Firmware Version[ \t]*:/         { ver = val($0) }
        /^[ \t]*Git Hash[ \t]*:/                 { git = val($0) }
        /^[ \t]*Build String[ \t]*:/             { bld = val($0) }
        /Git Version[ \t]*:/                     { drv = val($0); exit }
        END { print "FW:" dev "|" ver "|" git "|" bld "|" drv }
    ' "$p" 2>/dev/null
done
"""

# ---------------------------------------------------------------------------
# Ansible inventory
# ---------------------------------------------------------------------------
HOSTS_FILE = "/sdf/home/p/psrel/git/daqnodeconfig/ansible/hosts"


def _expand_range(pattern: str) -> list:
    """Expand 'prefix[01:10]' → ['prefix01', …, 'prefix10']."""
    m = re.match(r"^([^\[]+)\[(\d+):(\d+)\]$", pattern)
    if not m:
        return [pattern]
    prefix, start_s, end_s = m.group(1), m.group(2), m.group(3)
    width = max(len(start_s), len(end_s))
    return [f"{prefix}{i:0{width}d}" for i in range(int(start_s), int(end_s) + 1)]


def parse_ansible_hosts(path: str) -> dict:
    """
    Parse an Ansible INI-style inventory file.
    Returns {hostname: [group1, group2, …]} ordered by first appearance.
    """
    groups: dict = {}
    current_group = None
    try:
        with open(path) as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    # strip inline comment from header
                    ci = line.find("#")
                    if ci > 0:
                        line = line[:ci].strip()
                    if line.endswith("]"):
                        current_group = line[1:-1].strip()
                        groups.setdefault(current_group, [])
                    continue
                # host line – strip inline comment
                ci = line.find("#")
                if ci >= 0:
                    line = line[:ci].strip()
                if line and current_group is not None:
                    for h in _expand_range(line):
                        groups[current_group].append(h)
    except OSError:
        pass
    # invert to hostname → [groups]
    host_groups: dict = {}
    for grp, hosts in groups.items():
        for h in hosts:
            host_groups.setdefault(h, []).append(grp)
    return host_groups


# ---------------------------------------------------------------------------
# Column indices
# ---------------------------------------------------------------------------
COL_NODE = 0
COL_GROUPS = 1
COL_PROC = 2
COL_KO = 3
COL_SVC = 4
COL_CFG = 5
COL_PCIIDS = 6
COL_FWIMAGE = 7
COL_FWVER = 8
COL_EXEC = 9
NUM_COLS = 10

HEADERS = [
    "Node",
    "Groups",
    "/proc/datadev_*",
    "datadev.ko",
    "datadev.service",
    "cfgRxCount",
    "pci.ids",
    "Firmware image",
    "Firmware version",
    "ExecStart",
]

# ---------------------------------------------------------------------------
# CSV export columns
# ---------------------------------------------------------------------------
# Wider than the GUI table: the firmware build detail that the table only
# exposes as a tooltip gets its own columns here. Multi-card nodes keep the
# ' / ' separator used on screen, with the cards in datadev_N order so the
# positions line up across the firmware_* columns.
CSV_HEADERS = [
    "node",
    "groups",
    "reachable",
    "proc_datadev",
    "datadev_ko",
    "datadev_service",
    "cfg_rx_count",
    "pci_ids",
    "firmware_image",
    "firmware_version",
    "firmware_consistent",
    "card_count",
    "cards",
    "firmware_build_env",
    "firmware_build_server",
    "firmware_build_date",
    "firmware_builder",
    "firmware_git_hash",
    "dma_driver_version",
    "exec_start",
]


def clean_csv(value: str) -> str:
    """Normalise a display string for CSV: em-dash placeholder → empty cell."""
    if value is None:
        return ""
    value = str(value).strip()
    if value in ("—", "…"):
        return ""
    # Collapse the newlines used for multi-line table cells
    return " ".join(value.split())


def build_csv_rows(hostnames: list, results: dict, host_groups: dict) -> list:
    """
    Build the CSV body: one row per hostname, in hostname order.

    Independent of the table's current sort order. Nodes with no result yet
    (e.g. a refresh that was stopped early) are emitted as 'not checked'.
    """
    rows = []
    for hostname in hostnames:
        r = results.get(hostname)
        groups = ";".join(host_groups.get(hostname, []))
        if r is None:
            rows.append(
                [hostname, groups, "not checked"] + [""] * (len(CSV_HEADERS) - 3)
            )
            continue

        cards = r.get("fw_cards", [])

        def per_card(key, cards=cards):
            return " / ".join((c.get(key) or "") for c in cards)

        rows.append(
            [
                hostname,
                groups,
                "yes" if r["reachable"] else "no",
                clean_csv(r["proc"]),
                clean_csv(r["ko"]),
                clean_csv(r["svc"]),
                clean_csv(r["cfg"]),
                clean_csv(r["pciids"]),
                clean_csv(r["fwimage"]),
                clean_csv(r["fwver"]),
                # Flag mixed cards explicitly so it is filterable
                ""
                if not cards
                else ("yes" if r["fwimage_ok"] and r["fwver_ok"] else "no"),
                str(len(cards)) if r["reachable"] else "",
                " / ".join(f"datadev_{c['dev']}" for c in cards),
                per_card("env"),
                per_card("server"),
                per_card("date"),
                per_card("builder"),
                per_card("githash"),
                per_card("driver"),
                clean_csv(r["exec"]),
            ]
        )
    return rows


# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
GREEN = QColor("#c8f5c8")
RED = QColor("#f5c8c8")
GRAY = QColor("#cccccc")
AMBER = QColor("#f5e8c8")


# ---------------------------------------------------------------------------
# Firmware build-string parsing
# ---------------------------------------------------------------------------
# Build strings look like
#   DrpTDet: Vivado v2022.1, rdsrv301 (Ubuntu 20.04.6 LTS), \
#       Built Mon 29 Jan 2024 01:44:56 PM PST by weaver
# i.e. "<ImageName>: <BuildEnv>, <BuildServer>, Built <BuildDate> by <Builder>"
# (same layout surf's AxiVersion.parseBuildStamp expects).
BUILD_STRING_RE = re.compile(
    r"^(?P<image>[^:]+):\s*"
    r"(?P<env>[^,]+),\s*"
    r"(?P<server>.+?),\s*"
    r"Built\s+(?P<date>.+?)\s+by\s+(?P<builder>\S+)\s*$"
)


def parse_build_string(build: str) -> dict:
    """Split a firmware Build String into its parts; image name always set."""
    build = build.strip()
    m = BUILD_STRING_RE.match(build)
    if m:
        return m.groupdict()
    # Unparseable – fall back to the text before the first colon as the image
    image = build.split(":", 1)[0].strip() if ":" in build else build
    return {"image": image, "env": "", "server": "", "date": "", "builder": ""}


def format_fw_version(raw: str) -> str:
    """Normalise 'Firmware Version' to 0xMMmmPPPP, keeping odd values verbatim."""
    raw = raw.strip()
    try:
        return f"0x{int(raw, 16) if raw.lower().startswith('0x') else int(raw):07x}"
    except ValueError:
        return raw


def summarise(values: list) -> tuple:
    """
    Collapse one value per card into a cell text plus a 'consistent' flag.

    All cards equal → that value; mixed → values joined with ' / '.
    """
    uniq = []
    for v in values:
        if v not in uniq:
            uniq.append(v)
    if not uniq:
        return "—", True
    return " / ".join(uniq), len(uniq) == 1


# ---------------------------------------------------------------------------
# SSH worker
# ---------------------------------------------------------------------------


def check_node(hostname: str) -> dict:
    """Run all checks on one node over SSH. Returns a result dict."""
    result = {
        "hostname": hostname,
        "reachable": False,
        "proc": "—",
        "proc_names": "",
        "ko": "—",
        "svc": "—",
        "cfg": "—",
        "pciids": "—",
        "fwimage": "—",
        "fwimage_ok": True,
        "fwimage_tip": "",
        "fwver": "—",
        "fwver_ok": True,
        "fwver_tip": "",
        "fw_cards": [],  # structured per-card firmware detail (for CSV export)
        "exec": "—",
    }

    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "LogLevel=ERROR",
                hostname,
                "bash -s",
            ],
            input=CHECK_SCRIPT,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )

        # If stderr only (no stdout) we treat as unreachable
        if not proc.stdout.strip():
            result["exec"] = proc.stderr.strip()[:80] or "no output"
            return result

        result["reachable"] = True

        fw_cards = []  # one parsed dict per /proc/datadev_N

        for line in proc.stdout.splitlines():
            if line.startswith("FW:"):
                parts = line[3:].split("|")
                # dev|version|githash|buildstring|drivergitversion
                if len(parts) < 5:
                    continue
                dev, ver, githash, build, drvver = parts[:5]
                card = parse_build_string(build)
                card["dev"] = dev
                card["version"] = format_fw_version(ver) if ver else "—"
                card["githash"] = githash
                card["driver"] = drvver
                fw_cards.append(card)
            elif line.startswith("PROC:"):
                val = line[5:]
                if val == "NONE":
                    result["proc"] = "None"
                else:
                    names = [p.replace("/proc/", "") for p in val.split(",") if p]
                    result["proc"] = ", ".join(names)
                    result["proc_names"] = val
            elif line.startswith("KO:"):
                result["ko"] = line[3:]
            elif line.startswith("SVC:"):
                result["svc"] = line[4:]
            elif line.startswith("CFG:"):
                result["cfg"] = line[4:]
            elif line.startswith("PCIIDS:"):
                result["pciids"] = line[7:]
            elif line.startswith("EXEC:"):
                result["exec"] = line[5:]

        fw_cards.sort(key=lambda c: c["dev"])
        result["fw_cards"] = fw_cards

        if fw_cards:
            img_text, img_ok = summarise([c["image"] or "—" for c in fw_cards])
            ver_text, ver_ok = summarise([c["version"] for c in fw_cards])
            result["fwimage"] = img_text
            result["fwimage_ok"] = img_ok
            result["fwver"] = ver_text
            result["fwver_ok"] = ver_ok

            # Tooltips: full detail per card
            img_lines = []
            ver_lines = []
            for c in fw_cards:
                img_lines.append(f"datadev_{c['dev']}: {c['image'] or '—'}")
                if c["env"]:
                    img_lines.append(f"    built with {c['env']}")
                if c["server"]:
                    img_lines.append(f"    on {c['server']}")
                if c["date"]:
                    img_lines.append(f"    {c['date']}" + (f" by {c['builder']}" if c["builder"] else ""))
                ver_lines.append(f"datadev_{c['dev']}: {c['version']}")
                if c["githash"]:
                    ver_lines.append(f"    fw git hash: {c['githash']}")
                if c["driver"]:
                    ver_lines.append(f"    dma driver: {c['driver']}")
            result["fwimage_tip"] = "\n".join(img_lines)
            result["fwver_tip"] = "\n".join(ver_lines)
        else:
            # Reachable but no /proc/datadev_* to read firmware from
            result["fwimage"] = "N/A"
            result["fwver"] = "N/A"

    except subprocess.TimeoutExpired:
        result["exec"] = "timeout"
    except Exception as exc:
        result["exec"] = str(exc)[:80]

    return result


# ---------------------------------------------------------------------------
# Qt worker object (runs in a QThread)
# ---------------------------------------------------------------------------


class NodeChecker(QObject):
    result_ready = pyqtSignal(dict)
    progress = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, hostnames: list):
        super().__init__()
        self._hostnames = hostnames
        self._running = True

    def run(self):
        total = len(self._hostnames)
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            futures = {exe.submit(check_node, h): h for h in self._hostnames}
            for future in as_completed(futures):
                if not self._running:
                    break
                self.result_ready.emit(future.result())
                done += 1
                self.progress.emit(int(done * 100 / total))
        self.finished.emit()

    def stop(self):
        self._running = False


# ---------------------------------------------------------------------------
# Helper – styled table cell
# ---------------------------------------------------------------------------


def make_item(text: str, align=Qt.AlignCenter, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(align)
    if tooltip:
        item.setToolTip(tooltip)
    return item


def colour_item(item: QTableWidgetItem, colour: QColor):
    item.setBackground(colour)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("datadev Node Status Monitor")
        self.resize(1500, 900)

        self._host_groups = parse_ansible_hosts(HOSTS_FILE)
        self._checker = None
        self._checker_thread = None
        # hostname → latest result dict, kept for CSV export
        self._results = {}
        self._last_refresh = None

        # Derive _hostnames / _row_map from the default node set
        self._active_set = DEFAULT_NODE_SET
        self._apply_node_set(DEFAULT_NODE_SET)

        self._build_ui()
        self._populate_table()

    # ------------------------------------------------------------------
    # Node-set helpers
    # ------------------------------------------------------------------

    def _build_hostnames(self, set_name: str) -> list:
        cfg = NODE_SETS[set_name]
        return [f"{cfg['prefix']}{i:03d}" for i in cfg["range"]]

    def _apply_node_set(self, set_name: str):
        self._active_set = set_name
        self._hostnames = self._build_hostnames(set_name)
        self._row_map = {h: i for i, h in enumerate(self._hostnames)}
        # Results belong to the previous set – drop them
        self._results = {}
        self._last_refresh = None
        self._sync_export_enabled()

    def _sync_export_enabled(self):
        """Export is only meaningful once the current set has results."""
        # Called from _apply_node_set, which runs before _build_ui on startup
        if hasattr(self, "_export_btn"):
            self._export_btn.setEnabled(bool(self._results))

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # ---- top toolbar ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        # Node-set selector
        nodeset_box = QGroupBox("Node set")
        nodeset_layout = QHBoxLayout(nodeset_box)
        nodeset_layout.setSpacing(12)
        nodeset_layout.setContentsMargins(8, 4, 8, 4)
        self._nodeset_group = QButtonGroup(self)
        for idx, name in enumerate(NODE_SETS):
            rb = QRadioButton(name)
            rb.setChecked(name == DEFAULT_NODE_SET)
            rb.toggled.connect(self._on_nodeset_changed)
            self._nodeset_group.addButton(rb, idx)
            nodeset_layout.addWidget(rb)
        toolbar.addWidget(nodeset_box)
        toolbar.addSpacing(10)

        self._refresh_btn = QPushButton("Refresh All")
        self._refresh_btn.setFixedWidth(110)
        self._refresh_btn.clicked.connect(self._start_refresh)
        toolbar.addWidget(self._refresh_btn)

        self._export_btn = QPushButton("Export CSV…")
        self._export_btn.setFixedWidth(110)
        self._export_btn.setToolTip("Save the current table to a CSV file")
        self._export_btn.setEnabled(False)  # nothing to export until a refresh
        self._export_btn.clicked.connect(self._export_csv)
        toolbar.addWidget(self._export_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        self._progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toolbar.addWidget(self._progress)

        self._ts_label = QLabel("Not yet refreshed")
        self._ts_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        toolbar.addWidget(self._ts_label)

        root.addLayout(toolbar)

        # ---- legend ----
        legend = QHBoxLayout()
        for colour, label in [
            (GREEN, "OK / present"),
            (RED, "Missing / No"),
            (GRAY, "Unreachable"),
            (AMBER, "N/A / mixed firmware across cards"),
        ]:
            swatch = QLabel("  ")
            swatch.setFixedSize(18, 18)
            swatch.setStyleSheet(f"background:{colour.name()}; border:1px solid #888;")
            legend.addWidget(swatch)
            legend.addWidget(QLabel(label))
            legend.addSpacing(16)
        legend.addStretch()
        root.addLayout(legend)

        # ---- table ----
        self._table = QTableWidget()
        self._table.setColumnCount(NUM_COLS)
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setSortingEnabled(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setAlternatingRowColors(False)
        self._table.setWordWrap(True)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        hdr = self._table.horizontalHeader()
        for col in range(NUM_COLS - 1):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(COL_EXEC, QHeaderView.Stretch)
        # Image names get long (and longer still on mixed nodes) – cap the
        # column so ExecStart keeps a usable share of the width; the full
        # value stays available in the tooltip.
        hdr.setSectionResizeMode(COL_FWIMAGE, QHeaderView.Interactive)
        self._table.setColumnWidth(COL_FWIMAGE, 230)

        # Monospace font for ExecStart
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(8)
        self._table.setFont(mono)

        root.addWidget(self._table)

        # ---- status bar ----
        self._status = QStatusBar()
        self.setStatusBar(self._status)

    # ------------------------------------------------------------------
    # Node-set radio button handler
    # ------------------------------------------------------------------

    def _on_nodeset_changed(self, checked: bool):
        if not checked:
            return
        btn = self._nodeset_group.checkedButton()
        if btn is None:
            return
        name = btn.text()
        if name == self._active_set:
            return
        self._stop_checker()
        self._apply_node_set(name)
        self._ts_label.setText("Not yet refreshed")
        self._status.clearMessage()
        self._populate_table()

    def _stop_checker(self):
        """Gracefully stop any running checker thread."""
        if self._checker:
            self._checker.stop()
        if self._checker_thread and self._checker_thread.isRunning():
            self._checker_thread.quit()
            self._checker_thread.wait(3000)
        self._checker = None
        self._checker_thread = None
        self._refresh_btn.setEnabled(True)
        # Partial results from an interrupted refresh are still exportable
        self._sync_export_enabled()
        self._progress.setVisible(False)
        for btn in self._nodeset_group.buttons():
            btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self):
        self._table.setSortingEnabled(False)
        self._table.clearContents()
        self._table.setRowCount(len(self._hostnames))
        for row, hostname in enumerate(self._hostnames):
            self._table.setItem(row, COL_NODE, make_item(hostname))
            # Groups column – static, filled once from the inventory file
            grps = self._host_groups.get(hostname, [])
            grp_text = "\n".join(grps) if grps else "—"
            grp_item = make_item(
                grp_text, align=Qt.AlignLeft | Qt.AlignVCenter, tooltip="\n".join(grps)
            )
            self._table.setItem(row, COL_GROUPS, grp_item)
            # remaining dynamic columns
            for col in range(COL_PROC, NUM_COLS):
                self._table.setItem(row, col, make_item("—"))
        self._table.setSortingEnabled(True)

    def _reset_pending(self):
        """Mark all data cells as '…' while a refresh is running (skip Groups)."""
        self._table.setSortingEnabled(False)
        for row in range(self._table.rowCount()):
            for col in range(COL_PROC, NUM_COLS):  # skip COL_GROUPS
                item = self._table.item(row, col)
                item.setText("…")
                item.setBackground(QBrush())
                item.setToolTip("")
        self._table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    # Refresh logic
    # ------------------------------------------------------------------

    def _start_refresh(self):
        if self._checker_thread and self._checker_thread.isRunning():
            return

        self._refresh_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        for btn in self._nodeset_group.buttons():
            btn.setEnabled(False)
        self._results = {}
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status.showMessage(
            f"Checking {len(self._hostnames)} nodes ({self._active_set}) …"
        )
        self._reset_pending()

        self._checker = NodeChecker(self._hostnames)
        self._checker_thread = QThread()
        self._checker.moveToThread(self._checker_thread)

        self._checker_thread.started.connect(self._checker.run)
        self._checker.result_ready.connect(self._update_row)
        self._checker.progress.connect(self._progress.setValue)
        self._checker.finished.connect(self._on_done)

        self._checker_thread.start()

    def _on_done(self):
        if self._checker_thread:
            self._checker_thread.quit()
            self._checker_thread.wait()
        self._refresh_btn.setEnabled(True)
        self._sync_export_enabled()
        for btn in self._nodeset_group.buttons():
            btn.setEnabled(True)
        self._progress.setVisible(False)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_refresh = now
        self._ts_label.setText(f"Last refresh: {now}")

        # Tally for status bar
        reachable = ok_svc = ok_cfg = 0
        fw_images: dict = {}
        for row in range(self._table.rowCount()):
            ko_item = self._table.item(row, COL_KO)
            svc_item = self._table.item(row, COL_SVC)
            cfg_item = self._table.item(row, COL_CFG)
            img_item = self._table.item(row, COL_FWIMAGE)
            if ko_item and ko_item.text() not in ("—", "…", "unreachable"):
                reachable += 1
            if svc_item and svc_item.text() == "YES":
                ok_svc += 1
            if cfg_item and cfg_item.text() == "YES":
                ok_cfg += 1
            if img_item and img_item.text() not in ("—", "…", "N/A"):
                # A mixed node contributes each of its images separately
                for img in img_item.text().split(" / "):
                    fw_images[img] = fw_images.get(img, 0) + 1

        fw_summary = ", ".join(
            f"{img}×{n}"
            for img, n in sorted(fw_images.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        self._status.showMessage(
            f"Done {now}  |  {self._active_set}  |  "
            f"{reachable}/{len(self._hostnames)} reachable  |  "
            f"{ok_svc} with service  |  {ok_cfg} with cfgRxCount  |  "
            f"firmware: {fw_summary or 'none'}"
        )

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def _export_csv(self):
        if not self._results:
            QMessageBox.information(
                self, "Export CSV", "Nothing to export yet – run a refresh first."
            )
            return

        stamp = (self._last_refresh or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        default_name = "datadev_{}_{}.csv".format(
            self._active_set.split(" ")[0],
            stamp.replace("-", "").replace(":", "").replace(" ", "_"),
        )
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export table as CSV",
            os.path.join(os.path.expanduser("~"), default_name),
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return  # user cancelled
        if not os.path.splitext(path)[1]:
            path += ".csv"

        try:
            rows = self._csv_rows()
            with open(path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(CSV_HEADERS)
                writer.writerows(rows)
        except OSError as exc:
            QMessageBox.critical(
                self, "Export failed", f"Could not write {path}:\n{exc}"
            )
            return

        self._status.showMessage(f"Exported {len(rows)} rows to {path}", 10000)

    def _csv_rows(self) -> list:
        return build_csv_rows(self._hostnames, self._results, self._host_groups)

    # ------------------------------------------------------------------
    # Row update (called from worker thread via Qt signal)
    # ------------------------------------------------------------------

    def _update_row(self, result: dict):
        hostname = result["hostname"]
        self._results[hostname] = result
        # Find the current visual row for this hostname
        row = self._find_row(hostname)
        if row < 0:
            return

        self._table.setSortingEnabled(False)

        if not result["reachable"]:
            for col in range(1, NUM_COLS):
                item = self._table.item(row, col)
                item.setText("unreachable" if col == COL_KO else "—")
                colour_item(item, GRAY)
            # Put error hint in ExecStart column
            item = self._table.item(row, COL_EXEC)
            item.setText(result.get("exec", ""))
            self._table.setSortingEnabled(True)
            return

        # /proc/datadev_*
        proc_text = result["proc"]
        proc_item = self._table.item(row, COL_PROC)
        proc_item.setText(proc_text)
        if proc_text == "None":
            colour_item(proc_item, RED)
            proc_item.setToolTip("No /proc/datadev_* entries found")
        else:
            colour_item(proc_item, GREEN)
            proc_item.setToolTip(result.get("proc_names", ""))

        # datadev.ko
        ko_item = self._table.item(row, COL_KO)
        ko_item.setText(result["ko"])
        colour_item(ko_item, GREEN if result["ko"] == "YES" else RED)

        # datadev.service
        svc_item = self._table.item(row, COL_SVC)
        svc_item.setText(result["svc"])
        colour_item(svc_item, GREEN if result["svc"] == "YES" else RED)

        # cfgRxCount
        cfg_item = self._table.item(row, COL_CFG)
        cfg_item.setText(result["cfg"])
        if result["cfg"] == "YES":
            colour_item(cfg_item, GREEN)
        elif result["cfg"] == "N/A":
            colour_item(cfg_item, AMBER)
        else:
            colour_item(cfg_item, RED)

        # pci.ids
        pciids_item = self._table.item(row, COL_PCIIDS)
        pciids_item.setText(result["pciids"])
        colour_item(pciids_item, GREEN if result["pciids"] == "YES" else RED)

        # Firmware image / version – amber when cards on a node disagree
        for col, key in ((COL_FWIMAGE, "fwimage"), (COL_FWVER, "fwver")):
            item = self._table.item(row, col)
            text = result[key]
            item.setText(text)
            item.setToolTip(result.get(f"{key}_tip", "") or text)
            if text in ("N/A", "—"):
                colour_item(item, AMBER if text == "N/A" else RED)
            elif result.get(f"{key}_ok", True):
                colour_item(item, GREEN)
            else:
                colour_item(item, AMBER)

        # ExecStart – keep default background (no custom colour)
        exec_item = self._table.item(row, COL_EXEC)
        exec_item.setText(result["exec"])
        exec_item.setBackground(QBrush())  # clear any leftover colour
        exec_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        exec_item.setToolTip(result["exec"])

        self._table.setSortingEnabled(True)

    def _find_row(self, hostname: str) -> int:
        """Return the current visual row index for hostname (table may be sorted)."""
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_NODE)
            if item and item.text() == hostname:
                return row
        return -1

    def closeEvent(self, event):
        self._stop_checker()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_headless_csv(out_path: str, set_name: str) -> int:
    """Check every node in a set and write CSV, with no GUI. Returns exit code."""
    cfg = NODE_SETS[set_name]
    hostnames = [f"{cfg['prefix']}{i:03d}" for i in cfg["range"]]
    host_groups = parse_ansible_hosts(HOSTS_FILE)

    # Fail fast on an unwritable destination rather than after all the SSH work
    if out_path != "-":
        try:
            with open(out_path, "a"):
                pass
        except OSError as exc:
            print(f"error: cannot write {out_path}: {exc}", file=sys.stderr)
            return 1

    show_progress = sys.stderr.isatty()
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
        futures = {exe.submit(check_node, h): h for h in hostnames}
        for future in as_completed(futures):
            r = future.result()
            results[r["hostname"]] = r
            if show_progress:
                print(
                    f"\r{len(results)}/{len(hostnames)} checked",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
    if show_progress:
        print("", file=sys.stderr)

    rows = build_csv_rows(hostnames, results, host_groups)
    if out_path == "-":
        writer = csv.writer(sys.stdout)
        writer.writerow(CSV_HEADERS)
        writer.writerows(rows)
    else:
        try:
            with open(out_path, "w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(CSV_HEADERS)
                writer.writerows(rows)
        except OSError as exc:
            print(f"error: could not write {out_path}: {exc}", file=sys.stderr)
            return 1
        reachable = sum(1 for r in results.values() if r["reachable"])
        print(
            f"wrote {len(rows)} rows to {out_path} "
            f"({reachable}/{len(hostnames)} reachable)",
            file=sys.stderr,
        )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="datadev node status monitor (GUI, or --csv for headless export)"
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="check all nodes and write CSV to PATH ('-' for stdout), then exit; "
        "no GUI/display required",
    )
    parser.add_argument(
        "--node-set",
        metavar="NAME",
        help="node set for --csv: a prefix such as 'drp-srcf-cmp' or a full "
        "label. Default: " + DEFAULT_NODE_SET,
    )
    args = parser.parse_args()

    set_name = DEFAULT_NODE_SET
    if args.node_set:
        matches = [
            n
            for n in NODE_SETS
            if n == args.node_set or n.split(" ")[0] == args.node_set
        ]
        if not matches:
            parser.error(
                "unknown --node-set {!r}; choose from: {}".format(
                    args.node_set, ", ".join(n.split(" ")[0] for n in NODE_SETS)
                )
            )
        set_name = matches[0]

    if args.csv:
        sys.exit(run_headless_csv(args.csv, set_name))

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Slightly larger base font
    font = app.font()
    font.setPointSize(9)
    app.setFont(font)

    win = MainWindow()
    if set_name != DEFAULT_NODE_SET:
        for btn in win._nodeset_group.buttons():
            if btn.text() == set_name:
                btn.setChecked(True)
                break
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
