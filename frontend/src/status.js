// French labels of the execution and step statuses shown in the Studio.
export const STATUS_LABELS = {
  completed: 'terminé',
  failed: 'échec',
  skipped: 'sauté',
  running: 'en cours',
  pending: 'en attente',
  awaiting_approval: 'à approuver',
  cancelled: 'annulé',
};

export function statusLabel(status) {
  return STATUS_LABELS[status] || status;
}
