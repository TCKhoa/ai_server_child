from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict

# WebSocket connection pool
connected_clients: Dict[str, WebSocket] = {}


async def websocket_endpoint(websocket: WebSocket, device_id: str):
    """WebSocket endpoint handler"""
    await websocket.accept()

    # Override old socket
    connected_clients[device_id] = websocket

    print(f"📡 CONNECTED: {device_id}")

    try:
        while True:
            data = await websocket.receive_text()
            print("📩", data)

    except WebSocketDisconnect:
        print(f"❌ DISCONNECT: {device_id}")
        connected_clients.pop(device_id, None)


def get_connected_clients():
    """Get connected clients dict"""
    return connected_clients
