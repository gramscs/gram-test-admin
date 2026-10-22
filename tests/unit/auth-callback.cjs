const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { runInNewContext } = require('node:vm');
const source = readFileSync('app/static/js/admin/auth.js', 'utf8');

for (const hash of ['#access_token=private&refresh_token=private', '#refresh_token=private', '#error=access_denied&error_description=private']) {
  test('clears Supabase callback ' + hash.split('=')[0], () => {
    const calls = [];
    runInNewContext(source, {
      URLSearchParams,
      window: {
        location: { hash, pathname: '/admin/login', search: '?next=shipments' },
        history: { replaceState: (...args) => calls.push(args) },
      },
    });
    assert.deepEqual(calls, [[null, '', '/admin/login?next=shipments']]);
  });
}

test('preserves unrelated anchors', () => {
  runInNewContext(source, {
    URLSearchParams,
    window: { location: { hash: '#help' }, history: { replaceState: () => assert.fail('Unrelated anchor removed') } },
  });
});
