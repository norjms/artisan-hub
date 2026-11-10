// Slice states as people read them, and the chip style for each.
export const STATE = {
  none: ['Not sliced', ''],
  queued: ['Queued', ''],
  slicing: ['Slicing', 'warn'],
  review: ['Needs review', 'warn'],
  approved: ['Approved', 'ok'],
  failed: ['Failed', 'bad'],
  stale: ['Out of date', 'warn']
};
export const stateLabel = (s) => (STATE[s] || [s])[0];
export const stateCls = (s) => (STATE[s] || ['', ''])[1];

export const ORIENT = [
  ['as_is', 'As the file is'],
  ['largest_face', 'Largest flat face down'],
  ['auto', 'Let the slicer choose']
];

export const ENGINE = { orca: 'OrcaSlicer', prusa: 'PrusaSlicer' };

// The designer setting keys each engine uses (for the advanced options on the Print tab).
export const KEYS = {
  orca: { walls: 'wall_loops', infill: 'sparse_infill_density', supports: 'enable_support' },
  prusa: { walls: 'perimeters', infill: 'fill_density', supports: 'support_material' }
};

export const MATERIALS = ['PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PLA-CF', 'PETG-CF', 'PA', 'PC', 'PVA', 'HIPS'];

export async function waitJob(api, id, onstatus, tries = 480) {
  for (let i = 0; i < tries; i++) {
    await new Promise((r) => setTimeout(r, 2500));
    const j = await api.get(`/api/jobs/${id}`);
    if (j.state === 'done' || j.state === 'failed') return j;
    if (j.error && onstatus) onstatus(`Retrying: ${(j.error || '').split('\n')[0].slice(0, 140)}`);
  }
  return null;
}
