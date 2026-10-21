// The grounded path's honesty rules, against a stubbed backend. No network.
//
// The point of these tests is not that a number is produced - it is that a
// missing source produces a refusal, and that an operator-facing reason never
// gets read aloud to a farmer.

import { GroundedBackend } from '../js/grounded.js';

const results = [];
function check(name, condition, detail = '') {
  results.push({ name, pass: Boolean(condition), detail });
}

const CROP = {
  id: 'soybean',
  label: 'सोयाबीन',
  pop: {
    sowing: { window: ['06-20', '07-05'], window_basis: "ICAR-IISR 2023 Table 2, Central zone row: '20th June-5th July'" },
    seed_rate_kg_ha: [55, 65],
    fertilizer: { N: 25, P: 100, K: 50, S: 50 },
  },
  pop_status: 'verified',
};

const UNVERIFIED = {
  id: 'onion',
  label: 'कांदा',
  pop: null,
  pop_status: 'unavailable',
  pop_message: 'कांद्याची पेरणी पद्धत माहिती अद्याप उपलब्ध नाही.',
};

// Routes are matched by path so a 404 elsewhere cannot be mistaken for a pass.
// Every farm-scoped answer needs a session and a farm first, so those two
// routes are stubbed by default. Tests that care about them override.
const DEFAULT_ROUTES = {
  'POST /auth/register': { status: 201, ok: true, payload: { access_token: 'tok' } },
  'POST /farms': { status: 201, ok: true, payload: { id: 1 } },
};

function stubBackend(routes = {}) {
  const backend = new GroundedBackend('http://stub');
  backend.calls = [];
  const all = { ...DEFAULT_ROUTES, ...routes };
  backend.api = async (method, path, body) => {
    backend.calls.push({ method, path, body });
    for (const [pattern, response] of Object.entries(all)) {
      const [routeMethod, routePath] = pattern.split(' ');
      if (method === routeMethod && path.startsWith(routePath)) return response;
    }
    return { status: 404, ok: false, payload: { error: { code: 'not_found', message: 'no stub' } } };
  };
  backend.cropCache = [CROP, UNVERIFIED];
  return backend;
}

async function spoken(backend, question) {
  const out = [];
  for await (const s of backend.answer(question)) out.push(s);
  return out.join(' ');
}

// --- an unverified crop gets the explanation, never an invented number --------
{
  const b = stubBackend({});
  const said = await spoken(b, 'कांद्याची बियाणे किती');
  check('unverified crop refuses with the backend message',
    said.includes('अद्याप उपलब्ध नाही') && !said.includes('किलो प्रति हेक्टर'), said);
}

// --- a verified crop speaks the window, not the citation ---------------------
{
  const b = stubBackend({});
  const said = await spoken(b, 'सोयाबीन पेरणीची वेळ');
  check('window spoken as a date', said.includes('20 जून') && said.includes('5 जुलै'), said);
  check('seed rate spoken', said.includes('55 ते 65 किलो प्रति हेक्टर'), said);
  check('citation not read aloud', !said.includes('ICAR') && !said.includes('Table 2'), said);
  check('citation kept for the record', Boolean(b.lastSource?.pop?.sowing?.window_basis));
}

// --- market 503: farmer-facing refusal spoken, operator reason withheld -------
{
  const b = stubBackend({
    'GET /market/prices': {
      status: 503,
      ok: false,
      payload: { error: { code: 'data_unavailable', message: 'environment variable SAMPADA_DATA_GOV_IN_KEY is not set' } },
    },
  });

  const said = await spoken(b, 'सोयाबीनचा भाव काय आहे');
  check('503 spoken as a refusal, not an error', said.includes('भाव मिळाले नाहीत'), said);
  check('env var name never spoken aloud', !said.includes('SAMPADA_DATA_GOV_IN_KEY'), said);
  check('operator reason still captured', b.lastUnavailable?.includes('SAMPADA_DATA_GOV_IN_KEY'),
    b.lastUnavailable);
  check('days param respects the endpoint floor',
    b.calls.some((c) => c.path.includes('days=7')), JSON.stringify(b.calls.map((c) => c.path)));
}

// --- market with rows speaks modal price and says it is current ---------------
{
  const b = stubBackend({
    'GET /market/prices': {
      status: 200,
      ok: true,
      payload: {
        records: [
          { modal_price: 4100, min_price: 4000, max_price: 4200, market: 'Pune', unit: 'INR per quintal' },
        ],
      },
    },
  });

  const said = await spoken(b, 'सोयाबीनचा भाव');
  check('modal price spoken', said.includes('4100') && said.includes('क्विंटल'), said);
  check('current, not forecast', said.includes('सध्याची'), said);
}

// --- weather speaks the forecast and labels it an estimate -------------------
{
  const b = stubBackend({
    'GET /weather': {
      status: 200,
      ok: true,
      payload: {
        days: [
          { day: '2026-10-01', temp_max_c: 34.3, temp_min_c: 23.7, rainfall_mm: 0.8 },
          { day: '2026-10-02', temp_max_c: 34.6, temp_min_c: 24.6, rainfall_mm: 0 },
          { day: '2026-10-03', temp_max_c: 33.8, temp_min_c: 23.9, rainfall_mm: 0.7 },
        ],
      },
    },
  });

  const said = await spoken(b, 'हवामान कसे आहे');
  check('today spoken', said.includes('0.8 मिलीमीटर'), said);
  check('three-day total spoken', said.includes('1.5 मिलीमीटर'), said);
  check('labelled an estimate', said.includes('अंदाजित'), said);
}

// --- recommendation speaks the band, not a bare percentage -------------------
{
  const b = stubBackend({
    'POST /recommendations': {
      status: 200,
      ok: true,
      payload: {
        options: [
          { crop: { code: 'bajra', label: 'बाजरी' }, confidence: 0.27, confidence_band: 'LOW' },
          { crop: { code: 'moong', label: 'मूग' }, confidence: 0.25, confidence_band: 'LOW' },
        ],
        unavailable_inputs: [{ component: 'market_price' }, { component: 'soil' }],
        is_provisional: true,
        disclaimers: ['हा अंदाज आहे, हमी नाही.'],
      },
    },
  });

  const said = await spoken(b, 'काय पेरणी करावी');
  check('top crop spoken', said.includes('बाजरी'), said);
  check('low confidence said as a word', said.includes('कमी') && !said.includes('27%'), said);
  check('provisional flagged', said.includes('तात्पुरती'), said);
  check('backend disclaimer used verbatim', said.includes('हा अंदाज आहे, हमी नाही.'), said);
  check('second option spoken', said.includes('मूग'), said);
}

// --- soil with no evidence refuses rather than guessing ----------------------
{
  const b = stubBackend({
    'POST /farms': { status: 201, ok: true, payload: { id: 1 } },
    'POST /farms/1/soil/estimate': {
      status: 200,
      ok: true,
      payload: { top_soil_type: null, confidence: 0, confidence_band: 'LOW', needs_lab_test: true },
    },
  });

  const said = await spoken(b, 'माझी जमीन कोणती');
  check('no soil type invented', !said.includes('काळी') && !said.includes('तांबडी'), said);
  check('suggests a lab test', said.includes('परीक्षण'), said);
  check('farm-scoped route used',
    b.calls.some((c) => c.path === '/farms/1/soil/estimate'), JSON.stringify(b.calls.map((c) => c.path)));
}

// --- an off-topic question does not invent an answer ------------------------
{
  const b = stubBackend({});
  const said = await spoken(b, 'मला क्रिकेटची माहिती हवी आहे');
  check('off-topic deflected to what it can do', said.includes('समजला नाही'), said);
  check('no API call made for off-topic', b.calls.length === 0, JSON.stringify(b.calls));
}

// --- intent matching: price must not swallow a recommendation ----------------
{
  const b = stubBackend({
    'POST /recommendations': {
      status: 200,
      ok: true,
      payload: { options: [{ crop: { label: 'बाजरी' }, confidence: 0.6, confidence_band: 'MEDIUM' }], disclaimers: ['हा अंदाज आहे.'] },
    },
  });

  await spoken(b, 'काय पेरणी करावी असे वाटत आहे');
  check('recommendation intent not confused by unrelated words',
    b.calls.some((c) => c.path === '/recommendations') && !b.calls.some((c) => c.path.startsWith('/market')),
    JSON.stringify(b.calls.map((c) => c.path)));
}

// --- report -------------------------------------------------------------------
let failed = 0;
for (const r of results) {
  if (!r.pass) failed += 1;
  console.log(`${r.pass ? 'PASS' : 'FAIL'}  ${r.name}${r.pass || !r.detail ? '' : '  -> ' + r.detail}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
