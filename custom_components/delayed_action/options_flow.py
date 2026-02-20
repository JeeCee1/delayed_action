import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN, CONF_DOMAINS, ATTR_DOMAINS

_LOGGER = logging.getLogger(__name__)


class DelayedActionOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Delayed Action."""

    # FIX: Removed __init__ that accepted and stored config_entry manually.
    # self.config_entry is now provided automatically by the parent OptionsFlow
    # class since HA 2024.11. Passing it manually logs a deprecation warning
    # and will break in HA 2025.12.

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            _LOGGER.debug("%s options updated: %s", DOMAIN, user_input)
            # FIX: Removed hass.bus.fire('internal_get_config_response').
            # Config is now read directly from entry.options in __init__.py.
            return self.async_create_entry(title="", data=user_input)

        # FIX: self.config_entry is provided by the parent class automatically.
        options = self.config_entry.options
        domains = options.get(CONF_DOMAINS, ATTR_DOMAINS)

        # FIX: Use add_suggested_values_to_schema rather than baking default=
        # values into the schema directly. This is the current recommended
        # pattern per the HA developer docs and avoids stale values if the
        # form is shown more than once.
        schema = vol.Schema(
            {
                vol.Optional(CONF_DOMAINS): cv.multi_select(
                    {
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
                    }
                )
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_DOMAINS: domains}
            ),
        )
