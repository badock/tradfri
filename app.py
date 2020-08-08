#!/usr/bin/env python3

from pytradfri import Gateway
from pytradfri.api.libcoap_api import APIFactory
from pytradfri.error import PytradfriError
from pytradfri.util import load_json, save_json

import json

import uuid
import threading
import time
import flask
from flask_cors import CORS, cross_origin

from rich.console import Console
from rich.table import Table
from rich import print
from rich.panel import Panel
from rich.columns import Columns
from threading import Lock
lock = Lock()

GATEWAY_IP = "192.168.1.129"

CONFIG_FILE = "config.json"

app = flask.Flask(__name__)

cors = CORS(app)
app.config['CORS_HEADERS'] = 'Content-Type'


def observe(api, device):
    def callback(updated_device):
        light = updated_device.light_control.lights[0]
        print("Received message for: %s" % light)

    def err_callback(err):
        print(err)

    def worker():
        api(device.observe(callback, err_callback, duration=120))

    threading.Thread(target=worker, daemon=True).start()
    print('Sleeping to start observation task')
    time.sleep(1)


def get_gateway_and_api():
    # Assign configuration variables.
    # The configuration check takes care they are present.
    conf = load_json(CONFIG_FILE)

    try:
        identity = conf[GATEWAY_IP].get('identity')
        psk = conf[GATEWAY_IP].get('key')
        api_factory = APIFactory(host=GATEWAY_IP, psk_id=identity, psk=psk)
    except KeyError:
        identity = uuid.uuid4().hex
        api_factory = APIFactory(host=GATEWAY_IP, psk_id=identity)

        try:
            psk = api_factory.generate_psk(GATEWAY_SEC)
            print('Generated PSK: ', psk)

            conf[GATEWAY_IP] = {'identity': identity,
                                'key': psk}
            save_json(CONFIG_FILE, conf)
        except AttributeError:
            raise PytradfriError("Please provide the 'Security Code' on the "
                                 "back of your Tradfri gateway using the "
                                 "-K flag.")

    api = api_factory.request

    gateway = Gateway()
    return gateway, api


def fetch_description():
    gateway, api = get_gateway_and_api()

    devices_command = gateway.get_devices()
    devices_commands = api(devices_command)
    devices = api(devices_commands)

    groups_command = gateway.get_groups()
    groups_commands = api(groups_command)
    groups = api(groups_commands)

    device_index = dict(map(lambda x: (x.id, x), devices))

    result = []

    for room in groups:

        room_devices = [device_index.get(dev_id, None) for dev_id in room.member_ids]

        active_ambiance = api(room.mood())

        room_description = {
            "name": room.name,
            "bulbs": [],
            "ambiances": [],
            "id": room.id,
            "ambiance_active": active_ambiance.id
        }

        moods_command = gateway.get_moods(room.id)
        moods_commands = api(moods_command)
        moods = api(moods_commands)

        room_description["ambiances"] = [{"name": mood.name, "id": mood.id} for mood in moods]
        moods_str = "\n".join([mood.name for mood in moods])

        moods_panel = (Panel(moods_str, title="Moods"))
        console = Console(record=True)

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Lights", style="dim", width=24)
        table.add_column("Light", justify="right")
        table.add_column("Dimmer", justify="right")
        table.add_column("State", justify="right")

        for device in room_devices:
            if device.has_light_control:
                table.add_row(
                    device.name,
                    "light",
                    str(device.light_control.lights[0].dimmer),
                    "on" if device.light_control.lights[0].state else "off",
                )
                room_description["bulbs"] += [{
                    "name": device.name,
                    "dimmer": device.light_control.lights[0].dimmer,
                    "state": device.light_control.lights[0].state,
                    "id": device.id
                }]
            else:
                table.add_row(
                    device.name,
                    "not a light",
                    "-",
                    "-"
                )

        # table_str = table
        column = Columns([moods_panel, table])
        room_panel = Panel(column, title=room.name)
        console.print(room_panel)
        result += [room_description]

    return result


CACHED_RESPONSE = None
LAST_FETCH = None


def get_description():
    global LAST_FETCH
    global CACHED_RESPONSE
    lock.acquire()
    try:
        if CACHED_RESPONSE is None or (time.time() - LAST_FETCH) > 10.0:
            CACHED_RESPONSE = fetch_description()
            LAST_FETCH = time.time()
        description = CACHED_RESPONSE
    finally:
        lock.release()  # release lock
    return description


def invalidate_description():
    global CACHED_RESPONSE
    CACHED_RESPONSE = None


@app.route('/description.json')
@cross_origin()
def description():
    description = get_description()
    return json.dumps(description)


@app.route('/bulb/off/<room>/<bulb>')
@cross_origin()
def switchOffBulb(room, bulb):
    gateway, api = get_gateway_and_api()
    device_command = gateway.get_device(bulb)
    devices_commands = api(device_command)
    state_command = devices_commands.light_control.set_state(False)
    api(state_command)
    invalidate_description()
    return ""


@app.route('/bulb/on/<room>/<bulb>')
@cross_origin()
def switchOnBulb(room, bulb):
    gateway, api = get_gateway_and_api()
    device_command = gateway.get_device(bulb)
    devices_commands = api(device_command)
    state_command = devices_commands.light_control.set_state(True)
    api(state_command)
    invalidate_description()
    return ""


@app.route('/bulb/dimmer/<room>/<bulb>/<int:value>')
@cross_origin()
def setDimmerBulb(room, bulb, value):
    gateway, api = get_gateway_and_api()
    device_command = gateway.get_device(bulb)
    devices_commands = api(device_command)
    state_command = devices_commands.light_control.set_dimmer(value)
    api(state_command)
    invalidate_description()
    return ""


@app.route('/room/off/<room>')
@cross_origin()
def switchOffRoom(room):
    gateway, api = get_gateway_and_api()
    group_command = gateway.get_group(room)
    group_commands = api(group_command)
    for member_command in group_commands.members():
        member = api(member_command)
        if member.has_light_control:
            switchOffBulb(room, member.id)
    invalidate_description()
    return ""


@app.route('/room/on/<room>')
@cross_origin()
def switchOnRoom(room):
    gateway, api = get_gateway_and_api()
    group_command = gateway.get_group(room)
    group_commands = api(group_command)
    for member_command in group_commands.members():
        member = api(member_command)
        if member.has_light_control:
            switchOnBulb(room, member.id)
    invalidate_description()
    return ""


@app.route('/room/dimmer/<room>/<int:value>')
@cross_origin()
def setDimmerRoom(room, value):
    gateway, api = get_gateway_and_api()
    group_command = gateway.get_group(room)
    group_commands = api(group_command)
    for member_command in group_commands.members():
        member = api(member_command)
        if member.has_light_control:
            setDimmerBulb(room, member.id, value)
    invalidate_description()
    return ""


@app.route('/room/ambiance/<room>/<int:ambiance>')
@cross_origin()
def selectAmbianceRoom(room, ambiance):
    gateway, api = get_gateway_and_api()
    group_command = gateway.get_group(room)
    group_commands = api(group_command)
    api(group_commands.activate_mood(mood_id=ambiance))
    invalidate_description()
    return ""


if __name__ == "__main__":
    fetch_description()
    app.run()
