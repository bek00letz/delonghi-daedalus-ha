"""The drink list: reading De'Longhi's answers, one button per drink, photos, offline start."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.delonghi_daedalus.api.content import (
    Drink,
    locale_candidates,
    parse_drinks,
    parse_photos,
)
from custom_components.delonghi_daedalus.api.models import DaedalusAppliance
from custom_components.delonghi_daedalus.const import DOMAIN
from tests.helpers import SERIAL, StubLocal, entry_data, stored

PNG_START = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"
PNG = PNG_START + b"\1" * 200_000 + PNG_END  # big enough to arrive in pieces
CUT_OFF = PNG_START + b"\1" * 64  # the start of a photo, without its end
CAPABILITIES = {"SKU": "0132250187", "FWUIVersion": "1.1.15", "FWWiFiVersion": "1.1.25", "FWMainboardVersion": "1.0.0"}
PHOTO = "https://dam.delonghi.com/412x452/assets/{}.png"


def _item(drink_id: int, title: str | None, clusters: list[str], *, display: bool = True, original: str = "") -> dict:
    return {
        "title": original or title or "",
        "display": display,
        "drink": [
            {
                "drink_id": str(drink_id),
                "customized_title": title,
                "original_title": original or (title or "").replace(" ", ""),
                "cluster": clusters,
            }
        ],
    }


RECIPES = {
    "entries": [
        {
            "drinks": [
                _item(1, "Espresso", ["HOT", "COFFEE"]),
                _item(8, "Latte Macchiato", ["HOT", "COFFEE", "MILK"]),
                _item(39, None, ["HOT", "COFFEE", "MILK"], original="MilchKaffee"),
                _item(82, "Mug Latte Macchiato", ["HOME", "MUG HOT", "COFFEE", "MILK"]),  # as De'Longhi writes it
                _item(102, "Mug Iced Cappuccino", ["HOME", "MUG COLD", "COFFEE", "MILK"]),
                _item(230, None, ["HOT", "COFFEE", "CUSTOM"], original="CustomBev1"),
                _item(99, "Not on this machine", ["HOT"], display=False),
                {"title": "broken", "drink": [{"drink_id": "x"}]},
                "nonsense",
            ]
        }
    ]
}
PHOTOS = {
    "drinks_images": [
        {"drink_id": "1", "image_1x1": PHOTO.format(1)},
        {"drink_id": "8", "image_1x1": PHOTO.format(8)},
        {"drink_id": "39", "image_1x1": "http://insecure.example/39.png"},
    ],
    "drinks_description": [{"drink_id": "8", "drink_description": "Milk with a shot of espresso."}],
}


def test_reading_the_drink_list() -> None:
    drinks = {d.id: d for d in parse_drinks(RECIPES)}
    assert sorted(drinks) == [1, 8, 39, 82, 102, 230]
    assert drinks[8].name == "Latte Macchiato" and drinks[8].is_main
    assert drinks[39].name == "Milch Kaffee"  # no display name: made readable
    assert drinks[82].is_mug and not drinks[82].is_main  # "MUG HOT": two groups in one string
    assert drinks[102].is_mug and "COLD" in drinks[102].groups
    assert drinks[230].name is None and drinks[230].is_custom
    assert parse_drinks({"entries": []}) == [] and parse_drinks("nonsense") == []


def test_reading_the_photos() -> None:
    photos = parse_photos(PHOTOS)
    assert photos[8] == (PHOTO.format(8), "Milk with a shot of espresso.")
    assert photos[1] == (PHOTO.format(1), None)
    assert 39 not in photos  # only https links


@pytest.mark.parametrize(
    ("language", "country", "first"),
    [("en", "RO", ("RO", "en-ro")), ("en-GB", None, ("GB", "en-gb")), ("it", "IT", ("IT", "it-it")), (None, None, ("GB", "en-gb"))],
)
def test_languages_to_try(language, country, first) -> None:
    pairs = locale_candidates(language, country)
    assert pairs[0] == first and pairs[-1] == ("GB", "en-gb")
    assert len(pairs) == len(set(pairs))


def _list_from_delonghi() -> list[Drink]:
    photos = parse_photos(PHOTOS)
    return [Drink(d.id, d.name, d.groups, *photos.get(d.id, (None, None))) for d in parse_drinks(RECIPES)]


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=entry_data())
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> StubLocal:
    machines: list[StubLocal] = []
    with (
        patch(
            "custom_components.delonghi_daedalus.DaedalusCloudClient.async_get_devices",
            AsyncMock(return_value=[DaedalusAppliance("thing", SERIAL, "ECAM47080", "0132250187", "ONLINE")]),
        ),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=lambda **kw: machines.append(StubLocal(**kw)) or machines[-1],
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    # Ready to brew, so the buttons are available and show their details.
    machines[0].push({"Message": "StatusService", "Response": json.dumps({"Status": "Idle", "CurrMode": 7, "alarms": {}})})
    await hass.async_block_till_done()
    return machines[0]


def _button(hass: HomeAssistant, drink_id: int) -> str | None:
    return er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{SERIAL}_drink_{drink_id}")


async def _deliver_capabilities(hass: HomeAssistant, stub_cloud) -> None:
    stub_cloud.instances[-1].on_shadow("MachineCapabilities", CAPABILITIES)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_built_in_drinks_until_the_list_arrives(hass: HomeAssistant, entry, stub_cloud, stub_content) -> None:
    await _setup(hass, entry)
    assert hass.states.get(_button(hass, 16)).attributes["friendly_name"].endswith("Hot Water")
    assert _button(hass, 8) and not _button(hass, 82)

    stub_content.drinks = _list_from_delonghi()
    await _deliver_capabilities(hass, stub_cloud)
    registry = er.async_get(hass)

    assert hass.states.get(_button(hass, 8)).attributes["friendly_name"].endswith("Latte Macchiato")
    assert hass.states.get(_button(hass, 8)).attributes["description"] == "Milk with a shot of espresso."
    # Mug versions and custom slots exist but start switched off.
    assert registry.async_get(_button(hass, 82)).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(_button(hass, 230)).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    # Built-in drinks this machine doesn't list are gone.
    assert _button(hass, 16) is None


async def test_old_buttons_keep_their_entity_ids(hass: HomeAssistant, entry, stub_cloud, stub_content) -> None:
    registry = er.async_get(hass)
    old = registry.async_get_or_create("button", DOMAIN, f"{SERIAL}_brew_espresso", config_entry=entry)
    gone = registry.async_get_or_create("button", DOMAIN, f"{SERIAL}_brew_coffee", config_entry=entry)
    await _setup(hass, entry)
    assert registry.async_get(old.entity_id).unique_id == f"{SERIAL}_drink_1"

    stub_content.drinks = _list_from_delonghi()
    await _deliver_capabilities(hass, stub_cloud)
    assert _button(hass, 1) == old.entity_id
    assert registry.async_get(gone.entity_id) is None  # drink 2 isn't on this machine


async def test_photos_download_once_and_show_on_the_buttons(
    hass: HomeAssistant, entry, stub_cloud, stub_content, aioclient_mock
) -> None:
    aioclient_mock.get(PHOTO.format(1), content=PNG, headers={"Content-Type": "image/png"})
    aioclient_mock.get(PHOTO.format(8), content=CUT_OFF, headers={"Content-Type": "image/png"})
    machine = await _setup(hass, entry)
    stub_content.drinks = _list_from_delonghi()
    await _deliver_capabilities(hass, stub_cloud)

    picture = hass.states.get(_button(hass, 1)).attributes["entity_picture"]
    assert picture.startswith(f"/{DOMAIN}/drinks/{entry.entry_id}/1.png?v=")
    assert Path(hass.config.path(".cache", DOMAIN, entry.entry_id, "1.png")).read_bytes() == PNG
    assert "entity_picture" not in hass.states.get(_button(hass, 8)).attributes  # refused: cut off

    # The last drink shows its name from the list and its photo.
    machine.push({"Message": "BeverageRequestReport", "RecipeId": 1, "ProfileId": 1})
    await hass.async_block_till_done()
    last = next(s for s in hass.states.async_all("sensor") if s.entity_id.endswith("last_drink"))
    assert last.state == "Espresso" and last.attributes["entity_picture"] == picture

    calls = aioclient_mock.call_count
    await _deliver_capabilities(hass, stub_cloud)  # same machine, fresh list: nothing downloads again
    assert aioclient_mock.call_count == calls


async def test_saved_list_is_used_without_internet(hass: HomeAssistant, entry, hass_storage, stub_content) -> None:
    saved = [d.as_dict() for d in _list_from_delonghi()]
    stored(hass_storage, entry.entry_id, drinks={"key": [], "fetched": 0, "drinks": saved})
    await _setup(hass, entry)
    assert hass.states.get(_button(hass, 8)).attributes["friendly_name"].endswith("Latte Macchiato")
    assert _button(hass, 16) is None  # the built-in list isn't used once a real one is known
    assert stub_content.calls == 0  # nothing to ask without the machine's firmware


async def test_removal_deletes_the_photos(hass: HomeAssistant, entry, stub_cloud, stub_content, aioclient_mock) -> None:
    aioclient_mock.get(PHOTO.format(1), content=PNG, headers={"Content-Type": "image/png"})
    await _setup(hass, entry)
    stub_content.drinks = _list_from_delonghi()
    await _deliver_capabilities(hass, stub_cloud)
    folder = Path(hass.config.path(".cache", DOMAIN, entry.entry_id))
    assert folder.exists()
    with patch("custom_components.delonghi_daedalus.DaedalusCloudClient.async_sign_out", AsyncMock()):
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    assert not folder.exists()


async def test_the_card_reads_drinks_even_in_standby(
    hass: HomeAssistant, entry, stub_cloud, stub_content, aioclient_mock, hass_ws_client
) -> None:
    aioclient_mock.get(PHOTO.format(8), content=PNG, headers={"Content-Type": "image/png"})
    machine = await _setup(hass, entry)
    stub_content.drinks = _list_from_delonghi()
    await _deliver_capabilities(hass, stub_cloud)
    machine.push({"Message": "StatusService", "Response": json.dumps({"Status": "Standby", "CurrMode": 0, "alarms": {}})})
    await hass.async_block_till_done()
    assert hass.states.get(_button(hass, 8)).state == "unavailable"  # can't brew in standby

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/drinks"})
    reply = await client.receive_json()
    assert reply["success"]
    (only,) = reply["result"]["machines"]
    latte = next(d for d in only["drinks"] if d["id"] == 8)
    assert latte["entity_id"] == _button(hass, 8)
    assert latte["photo"].startswith(f"/{DOMAIN}/drinks/{entry.entry_id}/8.png")  # the photo still shows
    assert latte["groups"] == ["coffee", "hot", "milk"]
    assert only["status_entity_id"].startswith("sensor.") and only["power_entity_id"].startswith("switch.")
    assert list(only["controls"]) == [
        "power", "brew_profile", "coffee_temperature", "auto_off_timer",
        "water_hardness", "sound", "energy_saving", "cup_warmer",
    ]


async def test_mug_buttons_switched_on_by_0_8_0_are_switched_off_once(
    hass: HomeAssistant, entry, hass_storage, stub_content
) -> None:
    """0.8.0 saved mug groups as "MUG HOT" and switched those buttons on."""
    saved = [d.as_dict() for d in _list_from_delonghi()]
    for drink in saved:
        if drink["id"] in (82, 102):
            drink["groups"] = ["COFFEE", "HOME", "MILK", "MUG HOT" if drink["id"] == 82 else "MUG COLD"]
    stored(hass_storage, entry.entry_id, drinks={"key": [], "fetched": 0, "drinks": saved})
    registry = er.async_get(hass)
    mug = registry.async_get_or_create("button", DOMAIN, f"{SERIAL}_drink_82", config_entry=entry)
    latte = registry.async_get_or_create("button", DOMAIN, f"{SERIAL}_drink_8", config_entry=entry)
    await _setup(hass, entry)
    assert registry.async_get(mug.entity_id).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(latte.entity_id).disabled_by is None

    # Switched back on by the person: the repair doesn't run again.
    registry.async_update_entity(mug.entity_id, disabled_by=None)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await _setup(hass, entry)
    assert registry.async_get(mug.entity_id).disabled_by is None


async def test_cut_off_photos_from_0_8_0_download_again(
    hass: HomeAssistant, entry, hass_storage, stub_cloud, stub_content, aioclient_mock
) -> None:
    """0.8.0 kept only the first piece of each photo; those are found and replaced."""
    folder = Path(hass.config.path(".cache", DOMAIN, entry.entry_id))
    folder.mkdir(parents=True)
    (folder / "1.png").write_bytes(CUT_OFF)
    saved = [d.as_dict() for d in _list_from_delonghi()]
    stored(hass_storage, entry.entry_id, drinks={"key": [], "fetched": 0, "drinks": saved}, photos={"1": PHOTO.format(1)})
    aioclient_mock.get(PHOTO.format(1), content=PNG, headers={"Content-Type": "image/png"})
    await _setup(hass, entry)
    assert "entity_picture" not in hass.states.get(_button(hass, 1)).attributes  # the cut-off file isn't offered

    stub_content.drinks = _list_from_delonghi()
    await _deliver_capabilities(hass, stub_cloud)
    assert (folder / "1.png").read_bytes() == PNG
    assert "?v=" in hass.states.get(_button(hass, 1)).attributes["entity_picture"]


class _Pieces:
    """A response body arriving in pieces, as over a real network."""

    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    async def read(self, n: int = -1) -> bytes:
        return self.parts[0]  # like a socket: only what has arrived so far

    async def iter_chunked(self, n: int):
        for part in self.parts:
            yield part


class _PiecesSession:
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    def get(self, url: str, **kwargs: Any):
        parts = self.parts

        class Response:
            status = 200
            content_type = "image/png"
            headers: dict[str, str] = {}
            content = _Pieces(parts)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc: Any) -> bool:
                return False

        return Response()


@pytest.mark.parametrize(
    ("parts", "kept"),
    [
        ([PNG[:16_000], PNG[16_000:120_000], PNG[120_000:]], True),  # whole, in three pieces
        ([PNG[:16_000], PNG[16_000:120_000]], False),  # the end never arrived
        ([PNG_START + b"\1" * (2 * 1024 * 1024)] + [PNG_END], False),  # too big
    ],
)
async def test_a_photo_is_read_to_its_end(hass: HomeAssistant, parts: list[bytes], kept: bool) -> None:
    from custom_components.delonghi_daedalus.drinks import DrinkList

    drinks = DrinkList(hass, "entry", None, _PiecesSession(parts))  # type: ignore[arg-type]
    data = await drinks._async_fetch_photo(PHOTO.format(1))
    assert (data == b"".join(parts)) if kept else data is None
