// k6 ring check against the LIVE service URL during a rollout ring (SRS §12.3).
// 20 req/s for SOAK_SECONDS. Thresholds: failed < 1%, p95 < 400 ms, session isolation 100%.
// k6 exits 99 when a threshold fails.
import http from 'k6/http';
import { check } from 'k6';

const SOAK = Number(__ENV.SOAK_SECONDS || 120);
const USERS = (__ENV.DEMO_TOKENS || 'alpha:tok-alpha,beta:tok-beta').split(',').map(p => p.split(':'));

export const options = {
  scenarios: {
    ring: { executor: 'constant-arrival-rate', rate: Number(__ENV.RATE || 20), timeUnit: '1s',
            duration: `${SOAK}s`, preAllocatedVUs: 20, maxVUs: 60 },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<400'],
    'checks{probe:isolation}': ['rate==1.0'],
  },
  summaryTrendStats: ['avg', 'p(95)', 'max'],
};

export default function () {
  const [user, token] = USERS[Math.floor(Math.random() * USERS.length)];
  const r = http.get(`${__ENV.BASE_URL}/api/v1/balance`, {
    headers: { Authorization: `Bearer ${token}` }, tags: { name: 'balance' },
  });
  const cc = String(r.headers['Cache-Control'] || '');
  check(r, {
    'caller sees only their own balance': x => x.status === 200 && x.json('user_id') === user,
    'Cache-Control has private and no-store': () => cc.includes('private') && cc.includes('no-store'),
  }, { probe: 'isolation' });
}
