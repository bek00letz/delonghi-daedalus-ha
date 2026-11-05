/*
 * Coffee drinks card for De'Longhi My Coffee Lounge.
 *
 * Shipped with the integration and loaded on every dashboard. Add it with:
 *   type: custom:delonghi-drinks-card
 *   # optional: title, entry_id (with more than one machine)
 *
 * The drink list comes from Home Assistant (delonghi_daedalus/drinks); a drink
 * is made by pressing its button entity, so Home Assistant's safety checks apply.
 */

const TAG = "delonghi-drinks-card";
const REFRESH_MS = 10 * 60 * 1000;
const SKELETON_DELAY_MS = 300;

const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "coffee", label: "Coffee", test: (g) => g.has("coffee") && !g.has("milk") && !g.has("cold") },
  { key: "milk", label: "With milk", test: (g) => g.has("milk") && !g.has("cold") },
  { key: "cold", label: "Cold", test: (g) => g.has("cold") },
  { key: "other", label: "Tea & water", test: (g) => !g.has("coffee") && !g.has("milk") && !g.has("cold") },
];

const haptic = (kind) => window.dispatchEvent(new CustomEvent("haptic", { detail: kind }));

const store = {
  get(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      /* private mode: not remembered, still works */
    }
  },
};

const STYLE = `
  :host {
    --dl-radius: var(--ha-card-border-radius, 12px);
    --dl-gap: 8px;
    --dl-spring: cubic-bezier(0.2, 0.9, 0.3, 1);
    --dl-accent: var(--primary-color);
    --dl-text: var(--primary-text-color);
    --dl-text-2: var(--secondary-text-color);
    --dl-fill: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
    --dl-hairline: var(--divider-color, rgba(127, 127, 127, 0.2));
  }
  ha-card { padding: 16px 16px 12px; overflow: hidden; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; min-height: 28px; }
  h2 { margin: 0; font-size: 20px; line-height: 28px; font-weight: 600; letter-spacing: -0.01em;
       color: var(--dl-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status { flex: none; font-size: 14px; color: var(--dl-text-2); font-variant-numeric: tabular-nums; }
  .progress { height: 3px; margin: 8px 0 0; border-radius: 2px; background: var(--dl-fill); overflow: hidden;
              opacity: 0; transition: opacity 0.25s ease; }
  .progress.on { opacity: 1; }
  .progress > div { height: 100%; width: 0; background: var(--dl-accent); border-radius: inherit;
                    transition: width 0.4s var(--dl-spring); }
  .chips { display: flex; gap: 8px; margin: 12px -16px 4px; padding: 0 16px 4px; overflow-x: auto;
           scrollbar-width: none; scroll-padding: 16px; }
  .chips::-webkit-scrollbar { display: none; }
  .chip { flex: none; min-height: 32px; padding: 0 14px; border-radius: 16px; border: 0.5px solid var(--dl-hairline);
          background: transparent; color: var(--dl-text); font: inherit; font-size: 14px; font-weight: 500;
          cursor: pointer; transition: background-color 0.2s ease, color 0.2s ease, transform 0.2s var(--dl-spring); }
  .chip[aria-pressed="true"] { background: var(--dl-accent); border-color: transparent;
          color: var(--text-primary-color, #fff); }
  .chip:active { transform: scale(0.96); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: var(--dl-gap); margin-top: 8px; }
  .tile { display: flex; flex-direction: column; min-width: 0; padding: 0 0 8px; border: none; border-radius: 10px;
          background: transparent; color: var(--dl-text); font: inherit; text-align: center; cursor: pointer;
          transition: transform 0.25s var(--dl-spring), background-color 0.2s ease;
          -webkit-tap-highlight-color: transparent; }
  .tile:hover { background: var(--dl-fill); }
  .tile:active { transform: scale(0.96); }
  .photo { position: relative; aspect-ratio: 412 / 452; border-radius: 10px; background: var(--dl-fill);
           display: grid; place-items: center; overflow: hidden; }
  .photo img { width: 100%; height: 100%; object-fit: contain; opacity: 0; transition: opacity 0.3s ease; }
  .photo img.loaded { opacity: 1; }
  .photo ha-icon { --mdc-icon-size: 32px; color: var(--dl-text-2); }
  .name { margin: 6px 4px 0; font-size: 13px; line-height: 17px; font-weight: 500; display: -webkit-box;
          -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
  .skeleton .photo { animation: pulse 1.4s ease-in-out infinite; }
  .skeleton .name { height: 12px; width: 70%; margin: 8px auto 0; border-radius: 6px; background: var(--dl-fill); }
  @keyframes pulse { 50% { opacity: 0.55; } }
  .message { padding: 24px 8px 16px; text-align: center; color: var(--dl-text-2); font-size: 14px; line-height: 20px; }
  .message button { margin-top: 12px; }
  button:focus-visible { outline: 2px solid var(--dl-accent); outline-offset: 2px; }

  dialog { width: min(360px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 12px; border: none;
           border-radius: 20px; background: var(--card-background-color, var(--ha-card-background, #fff));
           color: var(--dl-text); box-shadow: 0 1px 2px rgba(0,0,0,0.08), 0 12px 40px rgba(0,0,0,0.24); }
  dialog[open] { animation: rise 0.32s var(--dl-spring); }
  dialog::backdrop { background: rgba(0, 0, 0, 0.32); animation: fade 0.2s ease; }
  @keyframes rise { from { opacity: 0; transform: translateY(12px) scale(0.97); } }
  @keyframes fade { from { opacity: 0; } }
  dialog .photo { border-radius: 8px; }
  dialog h3 { margin: 16px 4px 0; font-size: 22px; line-height: 28px; font-weight: 600; letter-spacing: -0.015em; }
  dialog p { margin: 6px 4px 0; font-size: 15px; line-height: 21px; color: var(--dl-text-2); }
  .actions { display: grid; gap: 8px; margin-top: 20px; }
  .primary, .secondary { min-height: 48px; padding: 0 16px; border-radius: 12px; border: none; font: inherit;
          font-size: 16px; font-weight: 600; cursor: pointer;
          transition: transform 0.2s var(--dl-spring), opacity 0.2s ease, background-color 0.2s ease; }
  .primary { background: var(--dl-accent); color: var(--text-primary-color, #fff); }
  .secondary { background: var(--dl-fill); color: var(--dl-text); }
  .primary:active:not(:disabled), .secondary:active:not(:disabled) { transform: scale(0.97); }
  .primary:disabled { opacity: 0.4; cursor: default; }
  .note { min-height: 20px; margin: 10px 4px 2px; font-size: 13px; line-height: 18px; color: var(--dl-text-2); text-align: center; }
  .note.error { color: var(--error-color, #d93025); }
  .close { position: absolute; top: 18px; right: 18px; width: 32px; height: 32px; border-radius: 16px; border: none;
           display: grid; place-items: center; background: rgba(0,0,0,0.35); color: #fff; cursor: pointer;
           backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }
  .close::before { content: ""; position: absolute; inset: -6px; }
  .close ha-icon { --mdc-icon-size: 18px; }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after, dialog[open], dialog::backdrop { animation: none !important; transition: none !important; }
  }
`;

class DelonghiDrinksCard extends HTMLElement {
  constructor() {
    super();
    this._root = this.attachShadow({ mode: "open" });
    this._machine = null;
    this._error = null;
    this._loading = false;
    this._filter = "all";
    this._open = null; // drink shown in the sheet
    this._busy = false;
    this._note = null; // message under the sheet's buttons: {text, error, clearWhenReady}
    this._pendingRender = false;
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._render();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._load();
    else this._updateLive();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    this._timer = window.setInterval(() => this._load(), REFRESH_MS);
    this._onVisible = () => document.visibilityState === "visible" && this._load();
    document.addEventListener("visibilitychange", this._onVisible);
  }

  disconnectedCallback() {
    window.clearInterval(this._timer);
    document.removeEventListener("visibilitychange", this._onVisible);
  }

  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    const skeleton = window.setTimeout(() => !this._machine && this._render(true), SKELETON_DELAY_MS);
    try {
      const { machines } = await this._hass.callWS({ type: "delonghi_daedalus/drinks" });
      const wanted = this._config && this._config.entry_id;
      this._machine = (wanted ? machines.find((m) => m.entry_id === wanted) : machines[0]) || null;
      this._error = null;
      if (this._machine) {
        this._filter = store.get(`${TAG}:filter:${this._machine.entry_id}`) || "all";
      }
    } catch (err) {
      this._error = err;
    } finally {
      window.clearTimeout(skeleton);
      this._loading = false;
    }
    // Never redraw under an open sheet; catch up when it closes.
    if (this._open) this._pendingRender = true;
    else this._render();
  }

  /* ---- what the card shows ---- */

  _visibleDrinks() {
    const states = this._hass.states;
    // Drinks whose button is switched off in Home Assistant have no state: leave them out.
    return this._machine.drinks.filter((d) => states[d.entity_id]);
  }

  _filters(drinks) {
    return FILTERS.filter((f) => f.key === "all" || drinks.some((d) => f.test(new Set(d.groups))));
  }

  _status() {
    const m = this._machine;
    const state = m && m.status_entity_id && this._hass.states[m.status_entity_id];
    if (!state) return "";
    if (state.state === "unavailable" || state.state === "unknown") return "Not reachable";
    const progress = this._progress();
    if (progress !== null) return `Making… ${progress}%`;
    return this._hass.formatEntityState ? this._hass.formatEntityState(state) : state.state;
  }

  _progress() {
    const m = this._machine;
    const state = m && m.progress_entity_id && this._hass.states[m.progress_entity_id];
    const value = state ? Number(state.state) : NaN;
    return Number.isFinite(value) && value > 0 && value < 100 ? Math.round(value) : null;
  }

  /** Why a drink can't start now, or null when it can. */
  _blocker(drink) {
    const button = this._hass.states[drink.entity_id];
    if (button && button.state !== "unavailable") return null;
    const m = this._machine;
    const power = m.power_entity_id && this._hass.states[m.power_entity_id];
    const status = m.status_entity_id && this._hass.states[m.status_entity_id];
    if (!status || status.state === "unavailable") return { text: "Home Assistant can’t reach the machine right now." };
    if (power && power.state === "off") return { text: "The machine is in standby.", turnOn: true };
    if (this._progress() !== null) return { text: "The machine is making a drink." };
    return { text: "The machine isn’t ready. Check it for a message, like an empty water tank." };
  }

  /* ---- drawing ---- */

  _render(skeleton = false) {
    if (!this._config) return;
    const title = this._config.title || (this._machine && this._machine.name) || "Coffee";
    this._root.innerHTML = `<style>${STYLE}</style>
      <ha-card>
        <header><h2></h2><span class="status" aria-live="polite"></span></header>
        <div class="progress" aria-hidden="true"><div></div></div>
        <div class="body"></div>
      </ha-card>
      <dialog aria-labelledby="sheet-title"></dialog>`;
    this._root.querySelector("h2").textContent = title;
    const dialog = this._root.querySelector("dialog");
    dialog.addEventListener("click", (e) => e.target === dialog && dialog.close());
    dialog.addEventListener("close", () => {
      this._open = null;
      if (this._pendingRender) {
        this._pendingRender = false;
        this._render();
      }
    });
    const body = this._root.querySelector(".body");

    if (skeleton) {
      body.innerHTML = `<div class="grid" aria-hidden="true">${'<div class="tile skeleton"><div class="photo"></div><div class="name"></div></div>'.repeat(6)}</div>`;
      return;
    }
    if (this._error) {
      body.innerHTML = `<div class="message">Couldn’t load the drinks.<br><button class="secondary">Try again</button></div>`;
      body.querySelector("button").addEventListener("click", () => this._load());
      return;
    }
    if (!this._hass || (!this._machine && this._loading)) return;
    if (!this._machine) {
      body.innerHTML = `<div class="message">No De’Longhi machine is set up in Home Assistant.</div>`;
      return;
    }

    const drinks = this._visibleDrinks();
    if (!drinks.length) {
      body.innerHTML = `<div class="message">No drinks yet. They appear once Home Assistant has read the machine’s drink list.</div>`;
      this._updateLive();
      return;
    }

    const filters = this._filters(drinks);
    if (!filters.some((f) => f.key === this._filter)) this._filter = "all";
    const chips = document.createElement("div");
    chips.className = "chips";
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Drink types");
    for (const f of filters) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.textContent = f.label;
      chip.setAttribute("aria-pressed", String(f.key === this._filter));
      chip.addEventListener("click", () => {
        haptic("selection");
        this._filter = f.key;
        store.set(`${TAG}:filter:${this._machine.entry_id}`, f.key);
        this._render();
      });
      chips.append(chip);
    }
    if (filters.length > 2) body.append(chips);

    const grid = document.createElement("div");
    grid.className = "grid";
    const test = FILTERS.find((f) => f.key === this._filter).test;
    for (const drink of drinks.filter((d) => test(new Set(d.groups)))) {
      grid.append(this._tile(drink));
    }
    body.append(grid);
    this._updateLive();
  }

  _photo(drink, eager = false) {
    const box = document.createElement("div");
    box.className = "photo";
    if (drink.photo) {
      const img = document.createElement("img");
      img.alt = "";
      img.decoding = "async";
      img.loading = eager ? "eager" : "lazy";
      img.addEventListener("load", () => img.classList.add("loaded"));
      img.addEventListener("error", () => img.replaceWith(this._icon(drink)));
      img.src = drink.photo;
      box.append(img);
    } else {
      box.append(this._icon(drink));
    }
    return box;
  }

  _icon(drink) {
    const icon = document.createElement("ha-icon");
    const state = this._hass.states[drink.entity_id];
    icon.icon = (state && state.attributes.icon) || "mdi:coffee";
    return icon;
  }

  _tile(drink) {
    const tile = document.createElement("button");
    tile.className = "tile";
    tile.append(this._photo(drink));
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = drink.name;
    tile.append(name);
    tile.addEventListener("click", () => {
      haptic("light");
      this._openSheet(drink);
    });
    return tile;
  }

  _updateLive() {
    if (!this._machine || !this._root.querySelector(".status")) return;
    this._root.querySelector(".status").textContent = this._status();
    const progress = this._progress();
    const bar = this._root.querySelector(".progress");
    bar.classList.toggle("on", progress !== null);
    bar.firstElementChild.style.width = `${progress || 0}%`;
    if (this._open) this._updateSheet();
  }

  /* ---- the sheet ---- */

  _openSheet(drink) {
    const dialog = this._root.querySelector("dialog");
    this._open = drink;
    this._busy = false;
    this._note = null;
    dialog.innerHTML = "";
    const close = document.createElement("button");
    close.className = "close";
    close.setAttribute("aria-label", "Close");
    close.innerHTML = '<ha-icon icon="mdi:close"></ha-icon>';
    close.addEventListener("click", () => dialog.close());
    const title = document.createElement("h3");
    title.id = "sheet-title";
    title.textContent = drink.name;
    dialog.append(this._photo(drink, true), close, title);
    if (drink.description) {
      const text = document.createElement("p");
      text.textContent = drink.description;
      dialog.append(text);
    }
    const actions = document.createElement("div");
    actions.className = "actions";
    const make = document.createElement("button");
    make.className = "primary";
    make.addEventListener("click", () => this._make(drink));
    const turnOn = document.createElement("button");
    turnOn.className = "secondary";
    turnOn.textContent = "Turn on";
    turnOn.addEventListener("click", () => this._turnOn());
    actions.append(make, turnOn);
    const note = document.createElement("div");
    note.className = "note";
    note.setAttribute("role", "status");
    dialog.append(actions, note);
    this._updateSheet();
    dialog.showModal();
    make.focus();
  }

  _setNote(text, error = false, clearWhenReady = false) {
    this._note = { text, error, clearWhenReady };
    this._updateSheet();
  }

  _updateSheet() {
    const dialog = this._root.querySelector("dialog");
    const drink = this._open;
    if (!dialog || !drink) return;
    const make = dialog.querySelector(".primary");
    const turnOn = dialog.querySelector(".secondary");
    const note = dialog.querySelector(".note");
    const blocker = this._blocker(drink);
    if (!this._busy) make.textContent = `Make ${drink.name}`;
    make.disabled = this._busy || Boolean(blocker);
    turnOn.hidden = !(blocker && blocker.turnOn);
    if (this._note && this._note.clearWhenReady && !blocker) this._note = null;
    note.classList.toggle("error", Boolean(this._note && this._note.error));
    note.textContent = this._note ? this._note.text : blocker ? blocker.text : "Put a cup under the spout first.";
  }

  async _make(drink) {
    if (this._busy) return;
    this._busy = true;
    const make = this._root.querySelector("dialog .primary");
    make.textContent = "Starting…";
    this._setNote("Starting…");
    try {
      await this._hass.callService("button", "press", { entity_id: drink.entity_id });
      haptic("success");
      make.textContent = "Started";
      this._setNote(`Your ${drink.name} is on its way.`);
      window.setTimeout(() => this._root.querySelector("dialog").close(), 1200);
    } catch (err) {
      haptic("failure");
      this._busy = false;
      this._setNote((err && err.message) || "The machine didn’t start the drink. Try again.", true);
    }
  }

  async _turnOn() {
    const m = this._machine;
    if (!m.power_entity_id) return;
    this._setNote("Turning on… You can start the drink once the machine is ready.", false, true);
    try {
      await this._hass.callService("switch", "turn_on", { entity_id: m.power_entity_id });
      haptic("light");
    } catch (err) {
      this._setNote((err && err.message) || "Couldn’t turn the machine on.", true);
    }
  }
}

if (!customElements.get(TAG)) {
  customElements.define(TAG, DelonghiDrinksCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: TAG,
    name: "Coffee drinks",
    description: "Make a drink on your De’Longhi coffee machine.",
    documentationURL: "https://github.com/bek00letz/delonghi-daedalus-ha#drinks-card",
  });
}
