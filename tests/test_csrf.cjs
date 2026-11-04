const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const origin='https://atlas.example.invalid';
const requests=[];
let submit;
class BrowserRequest extends Request {
  constructor(input, options) { super(typeof input === 'string' ? new URL(input, origin) : input, options); }
}
const context = {
  Request: BrowserRequest, URL,
  location: {origin},
  window: {fetch: (request) => { requests.push(request); return Promise.resolve({}); }},
  document: {
    querySelector: () => ({content:'synthetic-csrf-token'}),
    addEventListener: (event, callback) => { submit=callback; },
    createElement: () => ({})
  }
};
vm.createContext(context);
const script = fs.readFileSync(require('node:path').join(__dirname, '../modules/web/assets/csrf.js'), 'utf8');
vm.runInContext(script, context);
context.window.fetch('/config/yaml/save', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
assert.equal(requests[0].headers.get('X-CSRF-Token'), 'synthetic-csrf-token');
assert.equal(requests[0].headers.get('Content-Type'), 'application/json');
context.window.fetch('/api/articles');
assert.equal(requests[1].headers.has('X-CSRF-Token'), false);
context.window.fetch('https://external.invalid/', {method:'POST'});
assert.equal(requests[2].headers.has('X-CSRF-Token'), false);
let field;
submit({target:{method:'POST', action:origin+'/admin/logout',querySelector:()=>null,appendChild:(f)=>{field=f;}}});
assert.equal(field.name,'csrf_token');
assert.equal(field.value,'synthetic-csrf-token');
console.log('CSRF browser helper: same-origin POST, GET, external origin and form checks passed');
