/**
 * The words a question is retrieved by, as the backend picks them (app/domain/chat.py: keywords): lower-case words of
 * three letters or more that aren't stop words, at most six. Used to look for the answer outside the current scope.
 */
const STOP = new Set(
  `a about after again all also am an and any are as at be because been before being between both but by can could did do
does doing down during each few for from further had has have having he her here hers him his how i if in into is it its itself just
me more most my no nor not now of off on once only or other our ours out over own said same say says she should so some such tell
than that the their theirs them then there these they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours know think like really yeah okay right thing things get got going`.split(
    /\s+/,
  ),
);

export function keywords(question: string, n = 6): string[] {
  const out: string[] = [];
  for (const raw of question.toLowerCase().match(/[\p{L}\p{N}_'’-]+/gu) ?? []) {
    const w = raw.replace(/^['’-]+|['’-]+$/g, "");
    if (w.length >= 3 && !STOP.has(w) && !out.includes(w)) out.push(w);
  }
  return out.slice(0, n);
}
