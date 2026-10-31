// Keep the vehicle drawings and controls, using Home Assistant's theme and typography.

import { css } from "lit";

export const tokens = css`
  :host {
    --gac-land: var(--ha-card-background, var(--card-background-color, #ffffff));
    --gac-paper: var(--secondary-background-color, #f5f5f5);
    --gac-water: var(--primary-background-color, #fafafa);
    --gac-ink: var(--primary-text-color, #212121);
    --gac-muted: var(--secondary-text-color, #727272);
    --gac-hair: var(--divider-color, rgba(0, 0, 0, 0.12));
    --gac-edge: var(--ha-card-border-color, var(--divider-color, rgba(0, 0, 0, 0.24)));
    --gac-mw: #2e6db4;
    --gac-mw-case: #1b3f6b;
    --gac-mw-ink: #ffffff;
    --gac-green: #1f8a4c;
    --gac-green-case: #145c33;
    --gac-red: #d2352b;
    --gac-red-case: #8e1f18;
    --gac-red-ink: #b02a21;
    --gac-yellow: #f2c230;
    --gac-yellow-case: #8a6a0a;
    --gac-minor: var(--secondary-background-color, #f5f5f5);
    --gac-minor-case: var(--disabled-text-color, #bdbdbd);
    --gac-road: var(--secondary-text-color, #727272);
    --gac-road-case: var(--primary-text-color, #212121);
    --gac-lamp: var(--secondary-text-color, #727272);
    --gac-lamp-high: #ffffff;
    --gac-body: var(--secondary-background-color, #f5f5f5);
    --gac-glass: color-mix(in srgb, var(--primary-color, #03a9f4) 12%, var(--gac-land));
    --gac-label: var(--ha-font-family-heading, var(--ha-font-family-body, Roboto, Noto, sans-serif));
    --gac-text: var(--ha-font-family-body, Roboto, Noto, sans-serif);
    color-scheme: light;
  }
  :host([dark]) {
    --gac-land: var(--ha-card-background, var(--card-background-color, #1c1c1c));
    --gac-paper: var(--secondary-background-color, #282828);
    --gac-water: var(--primary-background-color, #111111);
    --gac-ink: var(--primary-text-color, #e1e1e1);
    --gac-muted: var(--secondary-text-color, #9b9b9b);
    --gac-hair: var(--divider-color, rgba(225, 225, 225, 0.12));
    --gac-edge: var(--ha-card-border-color, var(--divider-color, rgba(225, 225, 225, 0.24)));
    --gac-mw: #3f86d4;
    --gac-mw-case: #9cc4ef;
    --gac-mw-ink: #0c1a2b;
    --gac-green: #34a866;
    --gac-green-case: #9ad9b2;
    --gac-red: #e0493e;
    --gac-red-case: #f3a49d;
    --gac-red-ink: #f08a80;
    --gac-yellow: #f2c230;
    --gac-yellow-case: #f7dd8a;
    --gac-minor: var(--secondary-background-color, #282828);
    --gac-minor-case: var(--disabled-text-color, #6f6f6f);
    --gac-road: var(--secondary-text-color, #9b9b9b);
    --gac-road-case: var(--primary-text-color, #e1e1e1);
    --gac-lamp: var(--secondary-text-color, #9b9b9b);
    --gac-lamp-high: #ffffff;
    --gac-body: var(--secondary-background-color, #282828);
    color-scheme: dark;
  }
`;

export const base = css`
  :host {
    display: block;
  }
  * {
    box-sizing: border-box;
  }
  ha-card {
    display: block;
    background: var(--gac-land);
    color: var(--gac-ink);
    border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--gac-hair));
    border-radius: var(--ha-card-border-radius, 12px);
    box-shadow: var(--ha-card-box-shadow, none);
    overflow: hidden;
    font: 400 14px/1.4 var(--gac-text);
    container-type: inline-size;
    -webkit-font-smoothing: antialiased;
  }
  ::selection {
    background: var(--gac-mw);
    color: var(--gac-mw-ink);
  }
  .plate {
    min-height: 56px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 16px;
    border-bottom: 1px solid var(--gac-hair);
  }
  .ref {
    width: 24px;
    height: 24px;
    flex: none;
    border: 1px solid var(--gac-ink);
    display: grid;
    place-items: center;
  }
  .ref svg {
    width: 16px;
    height: 16px;
  }
  .name {
    font: 400 var(--ha-card-header-font-size, 24px)/1.2 var(--gac-label);
    letter-spacing: -0.012em;
    text-transform: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
  }
  .plate .lab {
    margin-left: auto;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 45%;
  }
  .lab {
    font: 500 12px/16px var(--gac-text);
    letter-spacing: normal;
    text-transform: none;
    color: var(--gac-muted);
  }
  .sec {
    padding: 12px 16px;
  }
  .sec + .sec {
    border-top: 1px solid var(--gac-hair);
  }
  .fig {
    font: 400 40px/1 var(--gac-text);
    font-variant-numeric: tabular-nums lining-nums;
    letter-spacing: normal;
  }
  .fig small,
  .unit {
    font-size: 18px;
    font-weight: 400;
    color: var(--gac-muted);
    letter-spacing: 0.02em;
  }
  .num {
    font-family: var(--gac-label);
    font-variant-numeric: tabular-nums lining-nums;
  }
  .muted {
    color: var(--gac-muted);
  }
  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  .fresh {
    min-height: 36px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 6px 0 16px;
    border-top: 1px solid var(--gac-hair);
  }
  .fresh-info {
    flex: 1;
    min-width: 0;
    min-height: 36px;
    display: flex;
    align-items: center;
    gap: 6px;
    font: 400 13px/1.3 var(--gac-text);
    color: var(--gac-muted);
    text-align: left;
    cursor: pointer;
  }
  .refresh {
    flex: none;
    width: 36px;
    height: 36px;
    display: grid;
    place-items: center;
    padding: 0;
    color: var(--gac-muted);
    background: none;
    border: 0;
    border-radius: 2px;
    cursor: pointer;
  }
  .refresh:hover {
    color: var(--gac-ink);
    background: color-mix(in srgb, var(--gac-land) 85%, var(--gac-ink));
  }
  .refresh svg {
    width: 18px;
    height: 18px;
  }
  .refresh.reading svg {
    animation: gac-spin 1s linear infinite;
  }
  @keyframes gac-spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .refresh.reading svg {
      animation: none;
    }
  }
  .refresh:focus-visible,
  .tap:focus-visible {
    outline: 2px solid var(--gac-mw);
    outline-offset: -2px;
  }
  .tap {
    cursor: pointer;
    background: none;
    border: 0;
    padding: 0;
    color: inherit;
    font: inherit;
    text-align: inherit;
  }
  .mark {
    width: 12px;
    height: 12px;
    flex: none;
    display: inline-block;
    vertical-align: -1px;
  }
  .note {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 14px;
    line-height: 1.4;
    margin: 0;
  }
  .note .mark {
    margin-top: 3px;
  }
  .note.refused {
    color: var(--gac-red-ink);
  }
  .duo {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .duo > * + * {
    border-left: 1px solid var(--gac-hair);
    padding-left: 16px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 48px;
  }
  .row + .row {
    border-top: 1px solid var(--gac-hair);
  }
  .row .what {
    flex: 1;
    min-width: 0;
  }
  .row .title {
    font-size: 15px;
    font-weight: 500;
  }
  .row .desc {
    font-size: 13px;
    color: var(--gac-muted);
    line-height: 1.35;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 4px;
  }
  .grid3 {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 128px), 1fr));
    gap: 8px;
  }
  .grid2 {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .empty {
    padding: 16px;
    font-size: 14px;
    color: var(--gac-muted);
  }
  .empty strong {
    display: block;
    color: var(--gac-ink);
    font: 600 18px/1.2 var(--gac-label);
    letter-spacing: normal;
    text-transform: none;
    margin-bottom: 4px;
  }
  @container (max-width: 330px) {
    .sec,
    .plate {
      padding-left: 12px;
      padding-right: 12px;
    }
    .fresh {
      padding-left: 12px;
    }
    .fig {
      font-size: 34px;
    }
  }
`;
