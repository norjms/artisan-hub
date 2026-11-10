const SELL = ['OWN', 'COMMERCIAL', 'CC0', 'BY', 'BY-SA', 'BY-ND', 'PR-COMMERCIAL', 'PR-COMMERCIAL-ND', 'GPL', 'LGPL', 'BSD', 'APACHE', 'CERN-OHL'];
export const SHORT = {
  OWN: 'Own design', COMMERCIAL: 'Commercial', CC0: 'CC0', BY: 'CC BY', 'BY-SA': 'CC BY-SA', 'BY-ND': 'CC BY-ND',
  'BY-NC': 'CC BY-NC', 'BY-NC-SA': 'CC BY-NC-SA', 'BY-NC-ND': 'CC BY-NC-ND', SDFL: 'MW Standard', 'MW-EXCLUSIVE': 'MW Exclusive',
  'PR-COMMERCIAL': 'Commercial Use', 'PR-COMMERCIAL-ND': 'Commercial (no deriv.)', GPL: 'GPL', LGPL: 'LGPL', BSD: 'BSD',
  APACHE: 'Apache 2.0', 'CERN-OHL': 'CERN OHL', OCL: 'OCL', 'PR-SDFL': 'Printables Standard', 'CXY-SL': 'Creality Standard',
  ARR: 'All rights reserved', UNKNOWN: 'License unknown'
};
export function licenseChip(code) {
  const c = code || 'UNKNOWN';
  return { label: SHORT[c] || c, cls: SELL.includes(c) ? 'ok' : c === 'UNKNOWN' ? 'warn' : 'bad', sell: SELL.includes(c) };
}
