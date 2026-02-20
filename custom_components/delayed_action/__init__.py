import logging
import uuid
from datetime import timedelta

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_call_later, async_track_point_in_time
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    ATTR_ENTITY_ID,
    ATTR_DELAY,
    ATTR_ACTION,
    ATTR_DATETIME,
    ATTR_ADDITIONAL_DATA,
    ATTR_TASK_ID,
    CONF_DOMAINS,
    ATTR_DOMAINS,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_DELAYED_ACTION = "execute"
SERVICE_CANCEL_ACTION = "cancel"
SERVICE_LIST_ACTIONS = "list"
SERVICE_GET_CONFIG = "get_config"

SERVICE_DELAY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_ACTION): cv.string,
        vol.Optional(ATTR_DELAY): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_DATETIME): cv.datetime,
        vol.Optional(ATTR_ADDITIONAL_DATA): dict,
    }
)

SERVICE_CANCEL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_TASK_ID): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_DOMAINS, default=ATTR_DOMAINS): vol.All(
                    cv.ensure_list, [cv.string]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Delayed Action component."""
    _LOGGER.info("Setting up Delayed Action component")

    # FIX: Use setdefault so that if async_setup_entry has already run and
    # initialised hass.data[DOMAIN], we don't overwrite the tasks dict.
    # This guards against KeyError regardless of setup ordering.
    hass.data.setdefault(
        DOMAIN,
        {
            "tasks": {},
            "domains": config.get(DOMAIN, {}).get(CONF_DOMAINS, ATTR_DOMAINS),
        },
    )

    # FIX: Removed hass.bus.async_listen('internal_get_config_response').
    # The original used the HA event bus as internal IPC between the options
    # flow and the integration. This is non-standard, the listener was never
    # cleaned up, and the data is already available via entry.options directly.

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Delayed Action from a config entry."""
    _LOGGER.info("Setting up Delayed Action from config entry")

    # FIX: Use setdefault so we don't overwrite an existing tasks dict if
    # async_setup already ran. Fixes intermittent KeyError on startup.
    hass.data.setdefault(DOMAIN, {"tasks": {}})
    hass.data[DOMAIN]["domains"] = entry.options.get(CONF_DOMAINS, ATTR_DOMAINS)

    # Register a listener so that if the user changes options, the in-memory
    # domains list updates immediately without requiring a restart.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def handle_delayed_action(call):
        entity_id = call.data[ATTR_ENTITY_ID]
        action = call.data[ATTR_ACTION]
        delay = call.data.get(ATTR_DELAY)
        scheduled_time = call.data.get(ATTR_DATETIME)
        additional_data = call.data.get(ATTR_ADDITIONAL_DATA, {})

        task_id = str(uuid.uuid4())

        if delay:
            action_data = {
                ATTR_ENTITY_ID: entity_id,
                ATTR_ACTION: action,
                ATTR_DELAY: delay,
                ATTR_TASK_ID: task_id,
                ATTR_ADDITIONAL_DATA: additional_data,
            }
            _LOGGER.info(
                "Scheduling %s for %s in %s seconds (task %s)",
                action, entity_id, delay, task_id,
            )
            due = dt_util.now() + timedelta(seconds=delay)
            # FIX: async_call_later already fires its callback in the event
            # loop. The original code wrapped it in call_soon_threadsafe which
            # is both unnecessary and triggers HA's thread-safety detector
            # (introduced in 2024.5). Call _handle_action directly.
            task = async_call_later(
                hass, delay, lambda _, d=action_data: _handle_action(hass, d)
            )
            _store_task(hass, entity_id, action, task_id, task, due)

        elif scheduled_time:
            # FIX: The original used datetime.now() which is timezone-naive.
            # If scheduled_time is timezone-aware (which cv.datetime produces),
            # the subtraction throws a TypeError. Use dt_util.now() instead,
            # which returns a timezone-aware datetime matching HA's timezone.
            now = dt_util.now()

            # cv.datetime may return a naive datetime if no timezone was
            # provided by the caller. Normalise it to be safe.
            if scheduled_time.tzinfo is None:
                scheduled_time = dt_util.as_local(scheduled_time)

            delay_seconds = (scheduled_time - now).total_seconds()
            if delay_seconds < 0:
                _LOGGER.error("Scheduled time is in the past.")
                return

            action_data = {
                ATTR_ENTITY_ID: entity_id,
                ATTR_ACTION: action,
                ATTR_TASK_ID: task_id,
                ATTR_ADDITIONAL_DATA: additional_data,
            }
            _LOGGER.info(
                "Scheduling %s for %s at %s (task %s)",
                action, entity_id, scheduled_time, task_id,
            )
            # FIX: Same call_soon_threadsafe removal as above.
            task = async_track_point_in_time(
                hass,
                lambda _, d=action_data: _handle_action(hass, d),
                scheduled_time,
            )
            _store_task(hass, entity_id, action, task_id, task, scheduled_time)

        else:
            _LOGGER.error("Either delay or datetime must be provided.")

    @callback
    def _handle_action(hass, action_data):
        entity_id = action_data[ATTR_ENTITY_ID]
        action = action_data[ATTR_ACTION]
        task_id = action_data[ATTR_TASK_ID]
        additional_data = action_data.get(ATTR_ADDITIONAL_DATA, {})

        entity_registry = async_get_entity_registry(hass)
        entity = entity_registry.async_get(entity_id)
        if not entity:
            _LOGGER.error("Entity %s not found in registry.", entity_id)
            return

        domain = entity.domain
        service_data = {"entity_id": entity_id}
        if additional_data:
            service_data.update(additional_data)

        # FIX: The original used hass.loop.call_soon_threadsafe to schedule
        # async_create_task, which is wrong — _handle_action is a @callback
        # running in the event loop already. call_soon_threadsafe is for
        # crossing from a thread INTO the event loop, not for use within it.
        hass.async_create_task(
            hass.services.async_call(domain, action, service_data)
        )
        _LOGGER.info("Executed %s for %s", action, entity_id)
        _remove_task(hass, entity_id, task_id)

    async def handle_cancel_action(call):
        entity_id = call.data.get(ATTR_ENTITY_ID)
        task_id = call.data.get(ATTR_TASK_ID)
        if _cancel_task(hass, entity_id, task_id):
            _LOGGER.info(
                "Cancelled scheduled action for entity_id=%s, task_id=%s",
                entity_id, task_id,
            )
        else:
            _LOGGER.error(
                "No scheduled action found for entity_id=%s, task_id=%s",
                entity_id, task_id,
            )

    async def handle_list_actions(call):
        entity_id = call.data.get(ATTR_ENTITY_ID)
        actions = _list_tasks(hass, entity_id)
        serialized = _serialize_actions(actions)
        _LOGGER.info("Scheduled actions: %s", serialized)
        hass.bus.fire(f"{DOMAIN}_list_actions_response", {"actions": serialized})

    async def handle_get_config(call):
        """Return current domain config via event so the frontend card can read it."""
        domains = hass.data[DOMAIN].get(CONF_DOMAINS, ATTR_DOMAINS)
        hass.bus.fire(
            f"{DOMAIN}_get_config_response",
            {CONF_DOMAINS: domains},
        )

    # --- helper functions ---------------------------------------------------

    def _store_task(hass, entity_id, action, task_id, task, due):
        if entity_id not in hass.data[DOMAIN]["tasks"]:
            hass.data[DOMAIN]["tasks"][entity_id] = {}
        hass.data[DOMAIN]["tasks"][entity_id][task_id] = {
            "action": action,
            "task": task,
            "task_id": task_id,
            "due": due,
        }

    def _remove_task(hass, entity_id, task_id):
        tasks = hass.data[DOMAIN]["tasks"]
        if entity_id in tasks and task_id in tasks[entity_id]:
            del tasks[entity_id][task_id]
            if not tasks[entity_id]:
                del tasks[entity_id]

    def _cancel_task(hass, entity_id=None, task_id=None):
        tasks = hass.data[DOMAIN]["tasks"]
        if entity_id:
            if entity_id not in tasks:
                return False
            if task_id:
                if task_id not in tasks[entity_id]:
                    return False
                tasks[entity_id][task_id]["task"]()
                _remove_task(hass, entity_id, task_id)
                return True
            else:
                for tid, task_data in list(tasks[entity_id].items()):
                    task_data["task"]()
                    _remove_task(hass, entity_id, tid)
                return True
        else:
            for eid in list(tasks.keys()):
                for tid, task_data in list(tasks[eid].items()):
                    task_data["task"]()
                    _remove_task(hass, eid, tid)
            return True

    def _list_tasks(hass, entity_id=None):
        if entity_id:
            return hass.data[DOMAIN]["tasks"].get(entity_id, {})
        return hass.data[DOMAIN]["tasks"]

    def _serialize_actions(actions):
        serialized = {}
        for eid, entity_tasks in actions.items():
            serialized[eid] = {}
            for tid, task_data in entity_tasks.items():
                serialized[eid][tid] = {
                    "action": task_data["action"],
                    "task_id": tid,
                    "due": task_data["due"].isoformat(),
                }
        return serialized

    # --- service registration -----------------------------------------------

    # FIX: Replaced async_register_admin_service (deprecated) with the
    # standard hass.services.async_register for all services.
    hass.services.async_register(
        DOMAIN, SERVICE_DELAYED_ACTION, handle_delayed_action,
        schema=SERVICE_DELAY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CANCEL_ACTION, handle_cancel_action,
        schema=SERVICE_CANCEL_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LIST_ACTIONS, handle_list_actions,
        schema=vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_id}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_CONFIG, handle_get_config,
        schema=vol.Schema({}),
    )

    _LOGGER.info(
        "Registered services: %s, %s, %s, %s",
        SERVICE_DELAYED_ACTION, SERVICE_CANCEL_ACTION,
        SERVICE_LIST_ACTIONS, SERVICE_GET_CONFIG,
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — refresh the in-memory domains list."""
    hass.data[DOMAIN]["domains"] = entry.options.get(CONF_DOMAINS, ATTR_DOMAINS)
    _LOGGER.info(
        "Delayed Action domains updated: %s",
        hass.data[DOMAIN]["domains"],
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up all services and pending tasks."""
    _LOGGER.info("Unloading Delayed Action config entry")

    # FIX: The original returned True immediately without doing any cleanup.
    # Services persisted after unload and pending tasks kept running.
    # Now we cancel all pending tasks, remove all services, and clear
    # hass.data so a subsequent reload starts clean.
    tasks = hass.data.get(DOMAIN, {}).get("tasks", {})
    for entity_id in list(tasks.keys()):
        for task_id, task_data in list(tasks[entity_id].items()):
            try:
                task_data["task"]()
            except Exception:  # noqa: BLE001
                pass

    hass.services.async_remove(DOMAIN, SERVICE_DELAYED_ACTION)
    hass.services.async_remove(DOMAIN, SERVICE_CANCEL_ACTION)
    hass.services.async_remove(DOMAIN, SERVICE_LIST_ACTIONS)
    hass.services.async_remove(DOMAIN, SERVICE_GET_CONFIG)

    hass.data.pop(DOMAIN, None)

    return True
