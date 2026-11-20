"""Dependency-free read-only Modbus TCP client (FC01, FC02, FC03, FC04)."""
import secrets
import socket
import struct


class ModbusError(Exception):
    pass


TABLE_FC = {"coil": 1, "di": 2, "hr": 3, "ir": 4}
MAX_COUNT = {"coil": 2000, "di": 2000, "hr": 125, "ir": 125}


def _recv_exact(sock, count):
    buffer = bytearray()
    while len(buffer) < count:
        part = sock.recv(count - len(buffer))
        if not part:
            raise ModbusError("PLC disconnected before completing the response")
        buffer.extend(part)
    return buffer


def read_table(host, port, unit_id, table, start, count, timeout=3.0):
    """Read a single zero-based span. Return list[int] for registers, list[bool] for bits."""
    if table not in TABLE_FC:
        raise ValueError("Unsupported Modbus table")
    if (type(start) is not int or type(count) is not int or type(unit_id) is not int
            or not 0 <= start < 65536 or not 1 <= count <= MAX_COUNT[table]
            or start + count > 65536 or not 0 <= unit_id <= 255):
        raise ValueError("Invalid Modbus address, count or unit ID")
    fc = TABLE_FC[table]
    transaction = secrets.randbelow(65536)
    request = struct.pack(">HHHBBHH", transaction, 0, 6, unit_id, fc, start, count)
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            hdr = _recv_exact(sock, 7)
            tid, protocol, length, reply_unit = struct.unpack(">HHHB", hdr)
            if tid != transaction or protocol != 0 or reply_unit != unit_id or not (3 <= length <= 254):
                raise ModbusError("Invalid Modbus MBAP header")
            pdu = _recv_exact(sock, length - 1)
    except (OSError, TimeoutError) as exc:
        raise ModbusError(f"Modbus TCP connection failed: {exc}") from exc

    if pdu[0] == (fc | 0x80):
        if len(pdu) != 2:
            raise ModbusError("Malformed Modbus exception")
        raise ModbusError(f"Modbus exception FC{fc:02d} code {pdu[1]}")
    size = 2 * count if table in ("hr", "ir") else (count + 7) // 8
    if len(pdu) != size + 2 or pdu[0] != fc or pdu[1] != size:
        raise ModbusError(f"Unexpected Modbus FC{fc} response size/function")
    raw = pdu[2:]
    if table in ("hr", "ir"):
        return list(struct.unpack(">" + "H" * count, raw))
    return [bool(raw[index // 8] & (1 << (index % 8))) for index in range(count)]
