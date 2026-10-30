// Name search shared by Market > Players and the pull-down search.

// "aj brown" finds A.J. Brown and "mc" finds McCaffrey: every word typed has to appear in the name.
export const normalize = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim();

export const nameMatches = (name: string, needle: string) => {
  const hay = normalize(name);
  return normalize(needle).split(' ').every((w) => hay.includes(w));
};
