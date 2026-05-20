"""Send a receipt to the thermal printer, or queue it.

Uses python-escpos for QR code support. Falls back to queueing if the
printer isn't reachable.
"""
from __future__ import annotations

import json
import socket
from datetime import datetime
from pathlib import Path

from escpos.printer import Network

QUEUE_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "queue"
CONNECT_TIMEOUT_SEC = 2.0
SEND_TIMEOUT_SEC = 8.0
QR_SIZE = 8  # 1-16; module size for the printed QR code
ROAST_LABEL = "scan to be roasted"


def is_reachable(ip: str, port: int = 9100,
                 timeout: float = CONNECT_TIMEOUT_SEC) -> bool:
    if not ip:
        return False
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def print_receipt(ip: str, lines: list[str], qr_url: str | None = None,
                  port: int = 9100) -> bool:
    """Send the receipt + optional QR + cut command to the printer."""
    try:
        p = Network(host=ip, port=port, timeout=SEND_TIMEOUT_SEC)
    except Exception as e:
        print(f"  [printer connect fail] {type(e).__name__}: {e}")
        return False
    try:
        for line in lines:
            p.text(line + "\n")
        if qr_url:
            p.text("\n")
            try:
                p.set(align="center")
                p.qr(qr_url, size=QR_SIZE, native=False)
                p.text(ROAST_LABEL + "\n")
                p.set(align="left")
            except Exception as qr_err:
                print(f"  [qr failed] {type(qr_err).__name__}: {qr_err}")
        # The cutter blade sits ~8 lines above the print head. We feed
        # plenty of blank paper before the cut so we don't slice the
        # bottom of this receipt OR the top of whatever prints next.
        p.text("\n" * 8)
        try:
            p.cut(feed=True, lines=4)  # 4 extra feed lines, then full cut
        except TypeError:
            p.cut()  # older python-escpos signature
        return True
    except Exception as e:
        print(f"  [printer send fail] {type(e).__name__}: {e}")
        return False
    finally:
        try:
            p.close()
        except Exception:
            pass


def queue_receipt(lines: list[str], qr_url: str | None = None,
                  session_id: int | None = None,
                  queue_dir: Path = QUEUE_DIR_DEFAULT) -> Path:
    queue_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    fname = f"{stamp}_{session_id or 'manual'}.json"
    path = queue_dir / fname
    path.write_text(json.dumps({
        "queued_at": datetime.now().astimezone().isoformat(),
        "session_id": session_id,
        "lines": lines,
        "qr_url": qr_url,
    }, ensure_ascii=False, indent=2))
    return path


def flush_queue(ip: str, port: int = 9100,
                queue_dir: Path = QUEUE_DIR_DEFAULT) -> tuple[int, int]:
    """Print queued receipts in chronological order. Stops at first failure."""
    if not queue_dir.exists():
        return (0, 0)
    if not is_reachable(ip, port):
        files = sorted(queue_dir.glob("*.json"))
        return (0, len(files))

    files = sorted(queue_dir.glob("*.json"))
    printed = 0
    for f in files:
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"  [queue corrupt] {f.name}: {e}")
            continue
        ok = print_receipt(ip, data["lines"],
                           qr_url=data.get("qr_url"), port=port)
        if not ok:
            break
        f.unlink(missing_ok=True)
        printed += 1
    remaining = len(list(queue_dir.glob("*.json")))
    return (printed, remaining)


def send_or_queue(cfg: dict, lines: list[str],
                  qr_url: str | None = None,
                  session_id: int | None = None) -> str:
    """Try to print; if printer not reachable, queue to disk."""
    ip = cfg["printer"].get("ip")
    port = cfg["printer"].get("port", 9100)
    if ip and is_reachable(ip, port):
        if print_receipt(ip, lines, qr_url=qr_url, port=port):
            return "printed"
    queue_receipt(lines, qr_url=qr_url, session_id=session_id)
    return "queued"


# Back-compat shims so existing callers still work
def print_lines(ip: str, lines: list[str], port: int = 9100) -> bool:
    return print_receipt(ip, lines, qr_url=None, port=port)


def queue_lines(lines: list[str], session_id: int | None = None,
                queue_dir: Path = QUEUE_DIR_DEFAULT) -> Path:
    return queue_receipt(lines, qr_url=None,
                          session_id=session_id, queue_dir=queue_dir)
