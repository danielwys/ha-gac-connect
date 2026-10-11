const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync('custom_components/gac_connect/captcha_view.py', 'utf8');
const html = source.match(/_PAGE = """([\s\S]*?)"""/)[1];
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function page() {
  const elements = Object.fromEntries(['s','p','xv','b','g','m'].map(id => [id, {
    style: {}, value: '0', textContent: '', innerHTML: '', disabled: false,
    appendChild(child) { this.textContent += child.textContent; },
    setAttribute() {},
  }]));
  const requests = [];
  const timers = [];
  const context = vm.createContext({
    URLSearchParams, location: { search: '?flow_id=synthetic' },
    document: { getElementById: id => elements[id], createElement: () => ({}) },
    fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve})),
    setTimeout: cb => timers.push(cb),
  });
  vm.runInContext(script, context);
  return {elements, requests, timers, context};
}
const response = (status, data) => ({status, ok: status < 400, json: async () => data});
const tick = () => new Promise(resolve => setImmediate(resolve));

test('successful POST is visible even after flow state is removed', async () => {
  const p = page();
  const post = vm.runInContext('g.onclick()', p.context);
  p.requests[1].resolve(response(200, {ok: true}));
  await post;
  assert.match(p.elements.m.textContent, /Verified/);
  assert.equal(p.elements.g.disabled, true);
  // A poll already in flight must not overwrite the successful POST.
  p.requests[0].resolve(response(404));
  await tick();
  assert.match(p.elements.m.textContent, /Verified/);
});
