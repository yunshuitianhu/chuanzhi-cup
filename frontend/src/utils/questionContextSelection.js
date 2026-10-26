/**
 * @typedef {{ start: string, end: string }} QuestionTimeRange
 */

const DATE_ONLY = /^(?:\d{4}|[+-]\d{6})-\d{2}-\d{2}$/;
const NAIVE_DATETIME = /^(?:\d{4}|[+-]\d{6})-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;
const AWARE_DATETIME = /^(?:\d{4}|[+-]\d{6})-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/i;

/**
 * @param {unknown} value
 * @returns {value is Record<string, unknown>}
 */
function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Parse only an epoch-millisecond number or a nonempty date string. In
 * particular, never coerce null, undefined, booleans, or empty strings to zero.
 *
 * @param {unknown} value
 * @returns {number | null}
 */
function timestamp(value) {
  let parsed;
  if (typeof value === 'number' && Number.isFinite(value)) {
    parsed = value;
  } else if (typeof value === 'string' && value.trim() !== '') {
    const text = value.trim();
    // ECMAScript date-only parsing defaults to UTC; the chart uses local dates.
    parsed = Date.parse(DATE_ONLY.test(text) ? `${text}T00:00:00` : text.replace(' ', 'T'));
  } else {
    return null;
  }

  // Date also checks the supported range and normalizes fractional milliseconds.
  const normalized = new Date(parsed).getTime();
  return Number.isFinite(normalized) ? normalized : null;
}

/**
 * @param {unknown} value
 * @returns {boolean}
 */
function isProvided(value) {
  return value !== null && value !== undefined &&
    !(typeof value === 'string' && value.trim() === '');
}

/**
 * @param {number} value
 * @param {number} minimum
 * @param {number} maximum
 * @returns {number}
 */
function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

/**
 * @param {number} percentage
 * @param {number} minimum
 * @param {number} maximum
 * @returns {number}
 */
function interpolate(percentage, minimum, maximum) {
  const bounded = clamp(percentage, 0, 100);
  // Keep the endpoints exact even when a very large span loses ms precision.
  if (bounded === 0) return minimum;
  if (bounded === 100) return maximum;
  return clamp(minimum + (maximum - minimum) * (bounded / 100), minimum, maximum);
}

/**
 * Serialize a naive axis using the same browser-local wall clock used to plot
 * it. Appending Z here would invent a timezone absent from the backend data.
 *
 * @param {number} value
 * @returns {string}
 */
function localIso(value) {
  const date = new Date(value);
  const year = date.getFullYear();
  const yearText = year >= 0 && year <= 9999
    ? String(year).padStart(4, '0')
    : `${year < 0 ? '-' : '+'}${String(Math.abs(year)).padStart(6, '0')}`;
  const day = [date.getMonth() + 1, date.getDate()].map(part => String(part).padStart(2, '0'));
  const time = [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map(part => String(part).padStart(2, '0'));
  return `${yearText}-${day.join('-')}T${time.join(':')}.${String(date.getMilliseconds()).padStart(3, '0')}`;
}

/**
 * Convert an ECharts datazoom event to an inclusive chart-time range.
 *
 * `times` contains the backend's original time strings. Invalid entries are
 * ignored, and percentages interpolate across the entire valid min/max time
 * span, regardless of input order or sample spacing. A complete explicit
 * startValue/endValue pair takes priority over percentages; an invalid or
 * reversed complete explicit pair is rejected. batch[0] takes precedence over
 * the top-level payload when both provide the same kind of range.
 *
 * Percentages are clamped to 0..100. Explicit values are clamped to the data
 * bounds too, so even an entirely out-of-bounds range collapses to a boundary
 * point. A reversed range is rejected before clamping.
 *
 * Naive ISO/space-separated axes use browser-local Date.parse semantics and
 * return local ISO without Z. Date-only values use local midnight. Aware axes
 * return UTC ISO; mixed aware/naive axes are rejected to avoid ambiguity. The
 * server must validate against the data timezone before using the range.
 *
 * @param {unknown} times Backend chart time strings (normally readonly string[]).
 * @param {unknown} event ECharts datazoom event or its batch wrapper.
 * @returns {QuestionTimeRange | null}
 */
export function timeRangeFromZoom(times, event) {
  if (!Array.isArray(times) || !isRecord(event)) return null;

  let minimum = Infinity;
  let maximum = -Infinity;
  /** @type {'naive' | 'aware' | undefined} */
  let axisTimezone;
  for (const time of times) {
    // The backend axis is strings; numbers here may instead be category indices.
    if (typeof time !== 'string') continue;
    const text = time.trim();
    const naive = DATE_ONLY.test(text) || NAIVE_DATETIME.test(text);
    if (!naive && !AWARE_DATETIME.test(text)) continue;
    const parsed = timestamp(text);
    if (parsed === null) continue;
    const timezone = naive ? 'naive' : 'aware';
    if (axisTimezone !== undefined && axisTimezone !== timezone) return null;
    axisTimezone = timezone;
    minimum = Math.min(minimum, parsed);
    maximum = Math.max(maximum, parsed);
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null;

  /** @type {Record<string, unknown>[]} */
  const candidates = [];
  if (Array.isArray(event.batch) && isRecord(event.batch[0])) {
    candidates.push(event.batch[0]);
  }
  candidates.push(event);

  /** @type {number | undefined} */
  let start;
  /** @type {number | undefined} */
  let end;
  for (const candidate of candidates) {
    if (!isProvided(candidate.startValue) || !isProvided(candidate.endValue)) continue;
    const explicitStart = timestamp(candidate.startValue);
    const explicitEnd = timestamp(candidate.endValue);
    if (explicitStart === null || explicitEnd === null || explicitStart > explicitEnd) {
      return null;
    }
    start = clamp(explicitStart, minimum, maximum);
    end = clamp(explicitEnd, minimum, maximum);
    break;
  }

  if (start === undefined || end === undefined) {
    for (const candidate of candidates) {
      if (typeof candidate.start !== 'number' || typeof candidate.end !== 'number' ||
          !Number.isFinite(candidate.start) || !Number.isFinite(candidate.end)) continue;
      if (candidate.start > candidate.end) return null;
      start = interpolate(candidate.start, minimum, maximum);
      end = interpolate(candidate.end, minimum, maximum);
      break;
    }
  }

  if (start === undefined || end === undefined) return null;
  return axisTimezone === 'naive'
    ? { start: localIso(start), end: localIso(end) }
    : { start: new Date(start).toISOString(), end: new Date(end).toISOString() };
}
