const KEY = 'patri.following';
const MAX = 20;

export function following() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((t) => t && t.no) : [];
  } catch {
    return [];
  }
}

export function isFollowing(no) {
  return following().some((t) => t.no === no);
}

export function toggleFollow(train) {
  const list = following();
  const at = list.findIndex((t) => t.no === train.no);
  const nowFollowing = at < 0;
  if (nowFollowing) list.unshift({ no: train.no, name: train.name || train.no });
  else list.splice(at, 1);
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    /* private mode: following just does not persist */
  }
  return nowFollowing;
}
