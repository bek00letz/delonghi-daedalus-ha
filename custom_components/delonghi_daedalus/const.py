"""Constants for the De'Longhi Daedalus Home Assistant integration.

Crafted with obsessive attention to detail, high reliability, and native
Apple-grade elegance.
"""

from typing import Final

DOMAIN: Final = "delonghi_daedalus"
MANUFACTURER: Final = "De'Longhi"

# Configuration and Entry Data Keys
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_DEVICE_IP: Final = "device_ip"
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_THING_NAME: Final = "thing_name"
CONF_MODEL: Final = "model"
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_RETRUST_MACHINE: Final = "retrust_machine"

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

# Settings Mapping
WATER_TEMP_LOW: Final = 1
WATER_TEMP_MEDIUM: Final = 2
WATER_TEMP_HIGH: Final = 3
WATER_TEMP_MAX: Final = 4

WATER_TEMP_LABELS: Final[dict[int, str]] = {
    WATER_TEMP_LOW: "Low",
    WATER_TEMP_MEDIUM: "Medium",
    WATER_TEMP_HIGH: "High",
    WATER_TEMP_MAX: "Maximum",
}

AUTO_OFF_15_MIN: Final = 15
AUTO_OFF_30_MIN: Final = 30
AUTO_OFF_1_HOUR: Final = 60
AUTO_OFF_2_HOURS: Final = 120
AUTO_OFF_3_HOURS: Final = 180

AUTO_OFF_LABELS: Final[dict[int, str]] = {
    AUTO_OFF_15_MIN: "15 minutes",
    AUTO_OFF_30_MIN: "30 minutes",
    AUTO_OFF_1_HOUR: "1 hour",
    AUTO_OFF_2_HOURS: "2 hours",
    AUTO_OFF_3_HOURS: "3 hours",
}

# Known Recipe IDs
RECIPE_ESPRESSO: Final = 1
RECIPE_COFFEE: Final = 2
RECIPE_LONG: Final = 3
RECIPE_DOPPIO_PLUS: Final = 4
RECIPE_AMERICANO: Final = 5
RECIPE_CAPPUCCINO: Final = 6
RECIPE_LATTE_MACCHIATO: Final = 7
RECIPE_CAFFELATTE: Final = 8
RECIPE_FLAT_WHITE: Final = 9
RECIPE_HOT_WATER: Final = 10
RECIPE_SIMPLE_COFFEE: Final = 33  # Coffee XL on Eletta Ultra

RECIPE_NAMES: Final[dict[int, str]] = {
    RECIPE_ESPRESSO: "Espresso",
    RECIPE_COFFEE: "Coffee",
    RECIPE_LONG: "Long",
    RECIPE_DOPPIO_PLUS: "Doppio+",
    RECIPE_AMERICANO: "Americano",
    RECIPE_CAPPUCCINO: "Cappuccino",
    RECIPE_LATTE_MACCHIATO: "Latte Macchiato",
    RECIPE_CAFFELATTE: "Caffelatte",
    RECIPE_FLAT_WHITE: "Flat White",
    RECIPE_HOT_WATER: "Hot Water",
    RECIPE_SIMPLE_COFFEE: "Coffee XL",
}

DEFAULT_PROFILE_ID: Final = 1
