"""Constants for the De'Longhi Daedalus Home Assistant integration.

Crafted with obsessive attention to detail, high reliability, and native
Apple-grade elegance.
"""

from typing import Final

DOMAIN: Final = "delonghi_daedalus"
MANUFACTURER: Final = "De'Longhi"

# Configuration and Entry Data Keys
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"  # only in setups from before v0.7; removed at next start
# Handed from the setup screens to the integration once, then moved into the
# private store (storage.py) and deleted from the config entry.
CONF_SESSION_HANDOFF: Final = "session"
CONF_TOKEN_HANDOFF: Final = "token"
CONF_FINGERPRINT_HANDOFF: Final = "machine_cert_sha256"
CONF_DEVICE_IP: Final = "device_ip"
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_THING_NAME: Final = "thing_name"
CONF_MODEL: Final = "model"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_CONFIRM_MACHINE: Final = "confirm_machine"

# Default Network Settings
DEFAULT_WS_PORT: Final = 443
DEFAULT_WS_PATH: Final = "/ws/lan2lan"
DEFAULT_POLL_INTERVAL: Final = 30  # seconds
DEFAULT_TIMEOUT: Final = 10  # seconds
DEFAULT_RETRY_DELAY: Final = 5  # seconds
MAX_RECONNECT_DELAY: Final = 60  # seconds

# SAP Gigya Identity Provider Endpoints
GIGYA_API_KEY: Final = "4_mXSplGaqrFT0H88TAjqJuA"
GIGYA_LOGIN_URL: Final = "https://accounts.eu1.gigya.com/accounts.login"
GIGYA_JWT_URL: Final = "https://accounts.eu1.gigya.com/accounts.getJWT"
GIGYA_ACCOUNT_INFO_URL: Final = "https://accounts.eu1.gigya.com/accounts.getAccountInfo"
GIGYA_JWT_EXPIRATION: Final = 7776000  # 90 days in seconds
GIGYA_LOGOUT_URL: Final = "https://accounts.eu1.gigya.com/accounts.logout"
# The sign-in session, as the app requests it: 90 days.
GIGYA_SESSION_EXPIRATION: Final = 7776000

# AWS Cloud Endpoints
AWS_GATEWAY_URL: Final = "https://8q8c9xktb0.execute-api.eu-central-1.amazonaws.com/dlg-prod"
# Production EU endpoint, from the app's MqttConfig (the a39pw88kkur5b5 one is De'Longhi's test server).
AWS_IOT_ATS_HOST: Final = "a2612mo23mfrw1-ats.iot.eu-central-1.amazonaws.com"
AWS_CUSTOM_AUTHORIZER: Final = "dlg-prod-token-authorizer"

# Named Shadows & MQTT Topics
SHADOW_MACHINE_STATUS: Final = "MachineStatus"
SHADOW_MACHINE_CAPABILITIES: Final = "MachineCapabilities"
TOPIC_SHADOW_GET: Final = "$aws/things/{thing_name}/shadow/name/{shadow_name}/get"
TOPIC_SHADOW_GET_ACCEPTED: Final = "$aws/things/{thing_name}/shadow/name/{shadow_name}/get/accepted"
TOPIC_SHADOW_UPDATE_ACCEPTED: Final = "$aws/things/{thing_name}/shadow/name/{shadow_name}/update/accepted"
TOPIC_COMMANDS_REQUEST: Final = "$aws/things/{thing_name}/commands/request"

# Official Application Identifier
APP_ID_MCL: Final = "MCL"

# Protocol Messages & Commands
MESSAGE_AUTH: Final = "auth"
MESSAGE_MACHINE_UI: Final = "MachineUI"
MESSAGE_BEVERAGE_REQUEST: Final = "BeverageRequest"
MESSAGE_SET_MACHINE_SETTINGS: Final = "setMachineSettings"

# Protocol Responses
RESPONSE_OK: Final = "OK"
RESPONSE_MACHINE_UI: Final = "MachineUIResponse"
RESPONSE_BEVERAGE_REQUEST: Final = "BeverageRequestResponse"
RESPONSE_BEVERAGE_REPORT: Final = "BeverageRequestReport"
RESPONSE_SET_SETTINGS: Final = "setMachineSettingsResponse"

# Standby & Power States
TURN_ON_OFF_STANDBY: Final = 1  # Put machine to sleep / standby
TURN_ON_OFF_WAKE: Final = 2     # Wake machine from standby

# Machine Operational States
STATE_STANDBY: Final = "standby"
STATE_READY: Final = "ready"
STATE_BREWING: Final = "brewing"
STATE_RINSING: Final = "rinsing"
STATE_OFFLINE: Final = "offline"

# Drink numbers, in De'Longhi's ECAM numbering: the same numbers De'Longhi's
# drink list uses (seen for the ECAM 470.85, 2026-11-05); 8, 16 and 33 also
# confirmed by a brew. The names stand in until that list has been read.
RECIPE_ESPRESSO: Final = 1
RECIPE_LONG: Final = 3
RECIPE_DOUBLE_ESPRESSO: Final = 4
RECIPE_DOPPIO_PLUS: Final = 5
RECIPE_AMERICANO: Final = 6
RECIPE_CAPPUCCINO: Final = 7
RECIPE_LATTE_MACCHIATO: Final = 8
RECIPE_CAFFELATTE: Final = 9
RECIPE_FLAT_WHITE: Final = 10
RECIPE_ESPRESSO_MACCHIATO: Final = 11
RECIPE_HOT_MILK: Final = 12
RECIPE_HOT_WATER: Final = 16
RECIPE_SIMPLE_COFFEE: Final = 33

RECIPE_NAMES: Final[dict[int, str]] = {
    RECIPE_ESPRESSO: "Espresso",
    RECIPE_LONG: "Filter Coffee",
    RECIPE_DOUBLE_ESPRESSO: "2x Espresso",
    RECIPE_DOPPIO_PLUS: "Doppio+",
    RECIPE_AMERICANO: "Americano",
    RECIPE_CAPPUCCINO: "Cappuccino",
    RECIPE_LATTE_MACCHIATO: "Latte Macchiato",
    RECIPE_CAFFELATTE: "Caffelatte",
    RECIPE_FLAT_WHITE: "Flat White",
    RECIPE_ESPRESSO_MACCHIATO: "Espresso Macchiato",
    RECIPE_HOT_MILK: "Hot Milk",
    RECIPE_HOT_WATER: "Hot Water",
    RECIPE_SIMPLE_COFFEE: "Coffee",
}

DEFAULT_PROFILE_ID: Final = 1
