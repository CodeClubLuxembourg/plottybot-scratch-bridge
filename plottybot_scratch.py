import signal
import asyncio
import websockets
import socket
import json
from queue import Queue, Empty

# Configuration
command_server_address = "127.0.0.1"
command_server_port = 1337
websocket_port = 8766
command_queue = Queue()
canvas_max_x = 0
canvas_max_y = 0

# Global shutdown flag
shutdown_event = asyncio.Event()

def convert_coordinates(x, y):
    converted_x = (x + 250) * canvas_max_x / 500
    converted_y = (y + 180) * canvas_max_y / 360
    return converted_x, converted_y

def send_command_to_hardware(command):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((command_server_address, command_server_port))
            s.sendall(command.encode('utf-8'))
            response = s.recv(1024)
        return response.decode('utf-8')
    except socket.error as e:
        print(f"Socket error: {e}")
        return "error"

# Process commands in the queue
async def command_consumer():
    global canvas_max_x, canvas_max_y
    calibrated = False
    while not shutdown_event.is_set():
        # Check if calibrated
        if not calibrated:
            print("Checking if plottybot is calibrated")
            # Get hardware status
            status = json.loads(send_command_to_hardware("get_status"))
            # output log for debugging: output only calibration_done, canvas_max_x, canvas_max_y
            print("Hardware status: ", status["calibration_done"], status["canvas_max_x"], status["canvas_max_y"])
            # If not calibrated, keep checking
            if not status["calibration_done"]:
                await asyncio.sleep(5)  # Check every 5 seconds
                continue
            else:
                canvas_max_x = status["canvas_max_x"]
                canvas_max_y = status["canvas_max_y"]
                print("Hardware calibrated and ready to plot commands on canvas size: ({}, {})".format(canvas_max_x, canvas_max_y))
                calibrated = True

        # Process commands if calibrated
        while calibrated and not shutdown_event.is_set():
            try:
                command = command_queue.get_nowait()
            except Empty:
                await asyncio.sleep(0.1)
                continue

            print("Sending command to hardware: {}".format(command))
            response = send_command_to_hardware(command)
            if response != "ok":
                print("Error sending command to hardware: {}".format(response))
                calibrated = False
                break

    print("Command consumer shutdown.")

# Websocket Server Logic
async def websocket_server(websocket, path):
    oldX = 0
    oldY = 0
    print("New Scratch client connected.")
    try:
        async for message in websocket:
            data = json.loads(message)
            print(f"Received message: {data}")
            if data["type"] == "goToXY":
                if data["oldX"] != oldX or data["oldY"] == oldY:
                    # If oldX or oldY has changed, send a penUp command and move to the new 'old' location
                    command_queue.put("pen_up")
                    x, y = convert_coordinates(data["oldX"], data["oldY"])
                    command_queue.put(f"go_to({x},{y})")
                    oldX = data["oldX"]
                    oldY = data["oldY"]

                # Send a penDown command and move to the new location
                command_queue.put("pen_down")
                x, y = convert_coordinates(data["x"], data["y"])
                command_queue.put(f"go_to({x},{y})")
            if data["type"] == "penUp":
                command_queue.put("pen_up")
            await websocket.send("ok")
    except websockets.exceptions.ConnectionClosed:
        # When Scratch client disconnects
        while not command_queue.empty():
            command_queue.get()
        print("Scratch client disconnected. Queue cleared.")

async def start_websocket_server():
    async with websockets.serve(websocket_server, '0.0.0.0', websocket_port):
        print("WebSocket server started on port 8766")
        await shutdown_event.wait()  # Run until shutdown_event is set
        print("Shutting down WebSocket server...")

async def main():
    # Start the websocket server and the command consumer
    websocket_server_task = asyncio.create_task(start_websocket_server())
    command_consumer_task = asyncio.create_task(command_consumer())

    # Wait for the shutdown signal
    await shutdown_event.wait()

    # Cancel the tasks
    websocket_server_task.cancel()
    command_consumer_task.cancel()

    # Wait for the tasks to finish
    try:
        await websocket_server_task
    except asyncio.CancelledError:
        pass

    try:
        await command_consumer_task
    except asyncio.CancelledError:
        pass

def shutdown_handler(signum, frame):
    print("Shutdown signal received. Cleaning up...")
    shutdown_event.set()  # Signal the event to shut down

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)  # For Ctrl+C
    signal.signal(signal.SIGTERM, shutdown_handler)  # For system kill command

    # Run the main function
    asyncio.run(main())
