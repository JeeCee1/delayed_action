import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN, CONF_DOMAINS, ATTR_DOMAINS
from .options_flow import DelayedActionOptionsFlowHandler

_LOGGER = logging.getLogger(__name__)


class DelayedActionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Delayed Action."""

    VERSION = 1
    # FIX: Removed CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH.
    # This constant was removed from HA and causes an AttributeError on load.
    # Connection class is now declared via iot_class in manifest.json.

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            # FIX: Removed hass.bus.fire('internal_get_config_response').
            # Config data is stored in the entry and read via entry.options.
            return self.async_create_entry(title="Delayed Action", data={})

        data_schema = self.add_suggested_values_to_schema(
            vol.Schema({
                vol.Optional(CONF_DOMAINS): cv.multi_select({
                    "automation": "Automation",
                    "climate": "Climate",
                    "cover": "Cover",
                    "fan": "Fan",
                    "humidifier": "Humidifier",
                    "input_boolean": "Input Boolean",
                    "input_select": "Input Select",
                    "lawn_mower": "Lawn Mower",
                    "light": "Light",
                    "lock": "Lock",
                    "media_player": "Media Player",
                    "scene": "Scene",
                    "script": "Script",
                    "select": "Select",
                    "switch": "Switch",
                    "vacuum": "Vacuum",
                    "water_heater": "Water Heater",
                }),
            }),
            {CONF_DOMAINS: ATTR_DOMAINS},
        )
        return self.async_show_form(step_id="user", data_schema=data_schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        # FIX: Removed config_entry argument. As of HA 2024.11 self.config_entry
        # is provided automatically by the parent class. Passing it manually
        # will break in HA 2025.12.
        return DelayedActionOptionsFlowHandler()
