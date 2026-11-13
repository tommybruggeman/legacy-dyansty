// True when the owner asked for trade advice (a deal, an offer, buy or sell), not when a trade is only
// the news ("how does Jefferson look now that Cousins was traded"). Mirrors is_trade_question in
// league_ai/routing.py, which also keeps trade talk out of non-trade answers; keep the two in step.
const TRADE_ASK = new RegExp(
  [
    "\\btrade (for|away|him|her|them|my|me|[a-z.'\\-]+ (for|to|and))\\b",
    '\\b(should|would|could|can|do) (i|we|you) (trade|sell|buy|move|shop|flip)\\b',
    '\\btrade (offer|value|target|idea|partner|package|ask|market|chip|proposal)s?\\b',
    '\\b(sell|buy)[ -](high|low|now|him|her|them)\\b',
    '\\b(find|make|propose|build|suggest|counter|accept|decline|any) (me )?(a |an |this |that |the |good )?trade\\b',
    '\\btrades? (that|to|with|i|we)\\b',
    '\\b(offers?|deal for|package|in return|straight up|straight swap|swap|worth trading|trading (for|away|him|her))\\b',
    '\\bin a trade\\b',
    '\\bwhat (can|could|would) (i|we) get for\\b',
    '\\btrade\\b.*\\b(fair|accept|decline|worth it|win|lose|lopsided)\\b',
    '\\btrade\\??$',
  ].join('|'),
);
const SWAP = /^[a-z.'\- ]{3,40} for [a-z.'\- ]{3,40}\??$/; // "Waddle for Devonta Smith?"

export function isTradeQuestion(question: string | undefined | null): boolean {
  const q = (question ?? '').toLowerCase().replace(/\s+/g, ' ').trim();
  return TRADE_ASK.test(q) || SWAP.test(q);
}
