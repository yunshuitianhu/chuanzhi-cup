import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { timeRangeFromZoom } from '../src/utils/questionContextSelection.js';

const times = [
  '2026-10-26T00:00:00Z',
  '2026-10-26T00:10:00Z',
  '2026-10-26T04:00:00Z',
];
const fullRange = {
  start: '2026-10-26T00:00:00.000Z',
  end: '2026-10-26T04:00:00.000Z',
};
const middleRange = {
  start: '2026-10-26T01:00:00.000Z',
  end: '2026-10-26T03:00:00.000Z',
};

test('complete explicit epoch values take priority over percentages', () => {
  assert.deepEqual(timeRangeFromZoom(times, {
    startValue: Date.parse(middleRange.start),
    endValue: Date.parse(middleRange.end),
    start: 0,
    end: 100,
  }), middleRange);
});

test('explicit ISO strings normalize offsets to UTC', () => {
  assert.deepEqual(timeRangeFromZoom(times, {
    startValue: '2026-10-26T09:00:00+08:00',
    endValue: '2026-10-26T11:00:00+08:00',
  }), middleRange);
});

test('batch percentages interpolate the whole time span, not sample indices', () => {
  assert.deepEqual(timeRangeFromZoom(times, {
    batch: [{ start: 25, end: 75 }],
  }), middleRange);
});

test('top-level percentages work and incomplete explicit values can fall back', () => {
  assert.deepEqual(timeRangeFromZoom(times, {
    startValue: middleRange.start,
    start: 25,
    end: 75,
  }), middleRange);
});

test('complete explicit values take priority across batch and top-level payloads', () => {
  assert.deepEqual(timeRangeFromZoom(times, {
    batch: [{ start: 0, end: 100 }],
    startValue: middleRange.start,
    endValue: middleRange.end,
  }), middleRange);
  assert.deepEqual(timeRangeFromZoom(times, {
    batch: [{ startValue: middleRange.start, endValue: middleRange.end }],
    start: 0,
    end: 100,
  }), middleRange);
});

test('batch[0] takes precedence over top-level percentages', () => {
  assert.deepEqual(timeRangeFromZoom(times, {
    batch: [{ start: 25, end: 75 }, { start: 0, end: 100 }],
    start: 0,
    end: 100,
  }), middleRange);
});

test('invalid time entries are ignored and unordered valid times define bounds', () => {
  assert.deepEqual(timeRangeFromZoom([
    times[2], null, undefined, '', ' ', 'invalid', Infinity, 0, times[0],
  ], { start: 25, end: 75 }), middleRange);
});

test('empty or entirely invalid axes do not produce a range', () => {
  for (const axis of [undefined, null, '2026-10-26', [], [null, undefined, '', ' ', 'invalid', 0]]) {
    assert.equal(timeRangeFromZoom(axis, { start: 0, end: 100 }), null);
  }
});

test('single valid time produces equal endpoints', () => {
  const expected = { start: fullRange.start, end: fullRange.start };
  assert.deepEqual(timeRangeFromZoom([times[0]], { start: 25, end: 75 }), expected);
  assert.deepEqual(timeRangeFromZoom([times[0]], {
    startValue: middleRange.start,
    endValue: middleRange.end,
  }), expected);
});

test('extreme supported dates preserve exact endpoints without precision overflow', () => {
  const start = new Date(-8.64e15).toISOString();
  const end = new Date(8.64e15 - 1).toISOString();
  assert.deepEqual(timeRangeFromZoom([start, end], { start: 0, end: 100 }), { start, end });
});

test('percentages clamp to 0..100 and explicit values clamp to chart bounds', () => {
  assert.deepEqual(timeRangeFromZoom(times, { start: -25, end: 125 }), fullRange);
  assert.deepEqual(timeRangeFromZoom(times, {
    startValue: '2026-10-25T00:00:00Z',
    endValue: '2026-10-27T00:00:00Z',
  }), fullRange);
  assert.deepEqual(timeRangeFromZoom(times, {
    startValue: '2026-10-25T00:00:00Z',
    endValue: '2026-10-25T01:00:00Z',
  }), { start: fullRange.start, end: fullRange.start });
});

test('reversed ranges are rejected before clamping or percentage fallback', () => {
  for (const event of [
    { start: 75, end: 25 },
    { start: 150, end: 120 },
    { startValue: middleRange.end, endValue: middleRange.start, start: 0, end: 100 },
  ]) {
    assert.equal(timeRangeFromZoom(times, event), null);
  }
});

test('null, undefined, blank strings, and booleans never become epoch zero', () => {
  const epochTimes = ['1970-01-01T00:00:00Z', '1970-01-01T00:00:01Z'];
  for (const value of [null, undefined, '', ' ', false, true]) {
    assert.equal(timeRangeFromZoom(epochTimes, { startValue: value, endValue: 1000 }), null);
    assert.equal(timeRangeFromZoom(epochTimes, { startValue: 0, endValue: value }), null);
    assert.equal(timeRangeFromZoom(epochTimes, { start: value, end: 100 }), null);
    assert.equal(timeRangeFromZoom(epochTimes, { start: 0, end: value }), null);
  }
  assert.deepEqual(timeRangeFromZoom(epochTimes, { startValue: 0, endValue: 1000 }), {
    start: '1970-01-01T00:00:00.000Z',
    end: '1970-01-01T00:00:01.000Z',
  });
});

test('invalid, missing, or unsupported events return null', () => {
  for (const event of [
    undefined, null, [], {}, { batch: [] }, { batch: [null] },
    { start: '25', end: '75' }, { start: NaN, end: 75 },
    { start: 25, end: Infinity }, { startValue: 'invalid', endValue: middleRange.end },
    { startValue: 8.65e15, endValue: 8.66e15 },
    { startValue: {}, endValue: middleRange.end },
  ]) {
    assert.equal(timeRangeFromZoom(times, event), null);
  }
});

test('Shanghai subprocess preserves naive wall time, local date-only, and aware UTC semantics', () => {
  const helperUrl = new URL('../src/utils/questionContextSelection.js', import.meta.url).href;
  const script = `
    import assert from 'node:assert/strict';
    import { timeRangeFromZoom } from ${JSON.stringify(helperUrl)};
    assert.equal(new Date('2026-10-26T00:00:00').getTimezoneOffset(), -480);
    const expectedNaive = {
      start: '2026-10-26T09:15:00.000',
      end: '2026-10-26T09:45:00.000',
    };
    for (const times of [
      ['2026-10-26T09:00:00', '2026-10-26T10:00:00'],
      ['2026-10-26 09:00:00', '2026-10-26 10:00:00'],
    ]) {
      assert.deepEqual(timeRangeFromZoom(times, { start: 25, end: 75 }), expectedNaive);
      assert.deepEqual(timeRangeFromZoom(times, {
        startValue: Date.parse('2026-10-26T09:15:00'),
        endValue: Date.parse('2026-10-26T09:45:00'),
      }), expectedNaive);
    }
    assert.deepEqual(timeRangeFromZoom(['2026-10-26', '2026-10-27'], { start: 0, end: 100 }), {
      start: '2026-10-26T00:00:00.000', end: '2026-10-27T00:00:00.000',
    });
    assert.deepEqual(timeRangeFromZoom(['2026-10-26', '2026-10-26 01:00:00'], {
      startValue: '2026-10-26', endValue: '2026-10-26 00:30:00',
    }), { start: '2026-10-26T00:00:00.000', end: '2026-10-26T00:30:00.000' });
    assert.deepEqual(timeRangeFromZoom([
      '2026-10-26T09:00:00+08:00', '2026-10-26T10:00:00+08:00',
    ], { start: 25, end: 75 }), {
      start: '2026-10-26T01:15:00.000Z', end: '2026-10-26T01:45:00.000Z',
    });
    assert.equal(timeRangeFromZoom([
      '2026-10-26T09:00:00', '2026-10-26T10:00:00+08:00',
    ], { start: 0, end: 100 }), null);
  `;
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
    encoding: 'utf8',
    env: { ...process.env, TZ: 'Asia/Shanghai' },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout || String(result.error));
});
