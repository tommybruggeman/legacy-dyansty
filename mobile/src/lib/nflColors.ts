// Primary and secondary colors for each NFL team, used for the player circles on roster rows.
export const NFL_COLORS: Record<string, [string, string]> = {
  ARI: ['#97233F', '#FFB612'], ATL: ['#A71930', '#000000'], BAL: ['#241773', '#9E7C0C'], BUF: ['#00338D', '#C60C30'],
  CAR: ['#0085CA', '#101820'], CHI: ['#0B162A', '#C83803'], CIN: ['#FB4F14', '#000000'], CLE: ['#311D00', '#FF3C00'],
  DAL: ['#003594', '#869397'], DEN: ['#FB4F14', '#002244'], DET: ['#0076B6', '#B0B7BC'], GB: ['#203731', '#FFB612'],
  HOU: ['#03202F', '#A71930'], IND: ['#002C5F', '#A2AAAD'], JAX: ['#006778', '#D7A22A'], KC: ['#E31837', '#FFB81C'],
  LV: ['#000000', '#A5ACAF'], LAC: ['#0080C6', '#FFC20E'], LAR: ['#003594', '#FFA300'], MIA: ['#008E97', '#FC4C02'],
  MIN: ['#4F2683', '#FFC62F'], NE: ['#002244', '#C60C30'], NO: ['#101820', '#D3BC8D'], NYG: ['#0B2265', '#A71930'],
  NYJ: ['#125740', '#FFFFFF'], PHI: ['#004C54', '#A5ACAD'], PIT: ['#101820', '#FFB612'], SF: ['#AA0000', '#B3995D'],
  SEA: ['#002244', '#69BE28'], TB: ['#D50A0A', '#34302B'], TEN: ['#0C2340', '#4B92DB'], WAS: ['#5A1414', '#FFB612'],
};

export const teamColors = (team: string | null | undefined): [string, string] => NFL_COLORS[(team || '').toUpperCase()] ?? ['#1D3A2C', '#3A3524'];

// Position badge colors on the player circles.
export const POS_COLORS: Record<string, string> = {
  QB: '#E07B6A', RB: '#5CC391', WR: '#6EA9E0', TE: '#E6B25A', FLX: '#B393DB', FLEX: '#B393DB', SF: '#B393DB', OP: '#B393DB', SUPER_FLEX: '#B393DB',
  K: '#C8BEAD', DEF: '#C8BEAD',
};
