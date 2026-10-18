"""What kind of named thing a name is, by rules: the type analysis gives each name its extractor finds (analyze.py).

The rules read the name and the few words around it, and need no model:
- PERSON: a title first (Dr, Ms, Professor, King) or a role before it (CEO, my colleague); a given name first ("Sarah
  Johnson", "Maria"); a name of two or three words followed by a verb of saying ("Sarah Connor said"); a named
  speaker of the namespace.
- ORG, EVENT, WORK, PLACE: the word a name ends or starts with (Acme Inc, University of Oxford; Web Summit, World Cup;
  Clean Air Act; Hudson River, Mount Fuji), or what it's called ("a company called Acme", "a town called Hope").
- PLACE: countries, regions, states and large cities (lexicon/places.txt); EVENT: holidays; DATE: days and months.
- ORG, PRODUCT: well-known organisations and products (lexicon/organisations.txt, products.txt), and for PRODUCT a
  name with a number in it (iPhone 16).
A name none of these place is a TERM: a single unknown word (a brand, a project) needs a model to tell. `ask` gives
such names to the decision model, with the lines they're said in (Settings › Analysis › Types the rules can't tell,
`analysis.typing`): by default only when a decision model is set up (Jev or Laya), never the language model.

The given names are mostly the US Census Bureau's frequent first names of 1990 (public domain), with others added by
hand; the places, organisations and products are written by hand (lexicon/). Words that are names but mostly something else on their own (Will,
Mark, Rose, Claude) count only as the first of several words.
"""

from __future__ import annotations

import functools
import logging
import pathlib
import re

from . import decide

HERE = pathlib.Path(__file__).parent / "lexicon"
log = logging.getLogger("lens")
TITLES = {
    "Dr",
    "Mr",
    "Mrs",
    "Ms",
    "Miss",
    "Prof",
    "Professor",
    "Sir",
    "Dame",
    "Lord",
    "Lady",
    "Senator",
    "President",
    "Minister",
    "Governor",
    "Mayor",
    "Judge",
    "Justice",
    "King",
    "Queen",
    "Prince",
    "Princess",
    "Pope",
    "Saint",
    "St",
    "Sheikh",
    "Imam",
    "Rabbi",
    "Father",
    "Rev",
    "Reverend",
    "Captain",
    "General",
    "Colonel",
    "Uncle",
    "Aunt",
    "Auntie",
}
# a role or relation said just before a name: "our CEO Maria", "my colleague Tom Hughes"
ROLES = set(
    """ceo cto cfo coo cmo cpo cio ciso vp founder cofounder co-founder president chairman chairwoman chairperson director
manager engineer developer designer colleague coworker co-worker friend boss wife husband son daughter brother sister
mother father mom mum dad uncle aunt auntie cousin grandma grandpa grandmother grandfather nephew niece teacher doctor
nurse lawyer attorney senator governor mayor judge coach author writer novelist poet artist singer rapper musician
actor actress journalist reporter host anchor analyst investor researcher scientist student intern assistant secretary
neighbour neighbor girlfriend boyfriend fiancé fiance fiancee spouse mentor tutor therapist dentist sergeant officer
detective pastor imam rabbi priest sheikh mr mrs ms dr prof""".split()
)
# verbs of saying after a name: "Sarah Connor said"
SAYS = set(
    """said says say told tells asked asks thinks thought wrote writes mentioned mentions explained explains replied
replies added adds noted notes argued argues suggested suggests agreed agrees believes believed claimed claims recalled
recalls insisted insists laughed smiled emailed texted messaged answered answers""".split()
)
# "a company called Acme", "a guy named Jim": the word before called or named says what the name is
CALLED = {
    **dict.fromkeys(
        "company firm startup business agency bank organisation organization charity nonprofit brand band team club "
        "school university college lab".split(),
        "ORG",
    ),
    **dict.fromkeys(
        "app tool product service platform model library framework plugin extension device software program website site gadget".split(),
        "PRODUCT",
    ),
    **dict.fromkeys(
        "town city village place country island street restaurant cafe café bar hotel neighbourhood neighborhood area region".split(),
        "PLACE",
    ),
    **dict.fromkeys("guy man woman girl boy person lady kid someone somebody gentleman chap dude fellow".split(), "PERSON"),
    **dict.fromkeys(
        "book film movie song album paper article podcast show series play novel essay poem documentary episode report".split(),
        "WORK",
    ),
    **dict.fromkeys("conference event festival meetup summit workshop hackathon".split(), "EVENT"),
}
ORG_END = re.compile(
    r"^(Inc|Ltd|Corp|Corporation|Company|Co|LLC|LLP|LP|PLC|Plc|AG|GmbH|SA|SAS|SpA|BV|NV|Pty|Group|Bank|Bancorp|"
    r"Therapeutics|Pharmaceuticals|Pharma|Labs?|Laboratories|University|Institute|Foundation|Agency|Department|"
    r"Committee|Council|Association|Society|Commission|Ministry|Office|Board|Partners|Capital|Ventures|Systems|"
    r"Technologies|Technology|Tech|Holdings|Hospital|Clinic|School|College|Academy|Airlines|Airways|Motors|Studios?|"
    r"Media|Entertainment|Pictures|Records|Press|Publishing|Times|Post|News|Journal|Networks?|Software|Solutions|"
    r"Services|Industries|Enterprises|Consulting|Energy|Insurance|Trust|Fund|Party|Union|Club|FC|Church|Museum|Army|"
    r"Navy|Corps|Police|Parliament|Senate|Congress|Government|Administration|Authority|Bureau|Federation|Alliance|"
    r"Coalition|League|Organi[sz]ation|Initiative|Cooperative|Robotics|Dynamics|Analytics|Electronics|Brands|Foods|"
    r"Center|Centre|Court|Electric|Mills|AI)$"
)
ORG_START = {
    "University",
    "Bank",
    "Department",
    "Ministry",
    "Institute",
    "Museum",
    "Church",
    "College",
    "School",
    "Office",
    "Board",
    "Council",
    "Committee",
    "Federation",
    "Association",
    "Society",
    "Commission",
    "Agency",
    "Academy",
    "Embassy",
    "Bureau",
    "Court",
}
EVENT_END = re.compile(
    r"^(Conference|Summit|Festival|Fest|Olympics|Olympiad|Games|Cup|Championships?|Expo|Fair|Week|Awards|Forum|"
    r"Symposium|Convention|War|Revolution|Elections?|Marathon|Hackathon|Meetup|Workshop|Webinar|Tournament|Series|"
    r"Prix|Bowl|Gala|Rally|Crisis|Pandemic|Massacre|Day|Eve|Uprising|Riots?|Keynote|Ceremony|Parade|Jubilee|Carnival|"
    r"Exhibition|Concert|Tour)$"
)
EVENT_START = {"Battle", "Siege", "Operation"}
WORK_END = re.compile(
    r"^(Act|Treaty|Accords?|Constitution|Manifesto|Declaration|Charter|Directive|Regulation|Bible|Testament|Gospel|"
    r"Report|Symphony|Agreement|Protocol|Doctrine|Deal|Law)$"
)
WORK_START = {"Treaty"}
PLACE_END = re.compile(
    r"^(Street|Road|Avenue|Boulevard|Lane|Square|Bridge|River|Lake|Mountains?|Island|Islands|Isles|Valley|County|City|"
    r"Province|Prefecture|Bay|Ocean|Sea|Beach|Airport|Station|Hills|Heights|Desert|Canyon|Peninsula|Coast|Harbou?r|"
    r"Falls|Park|Gardens?|Forest|Region|District|Village|Borough|Township|Plaza|Highway|Strait|Glacier|Volcano|Reef|"
    r"Gulf|Delta|Basin|Plateau|Plains|Creek|Tower|Palace|Castle|Cathedral|Mosque|Temple|Stadium|Arena|Hall|Mall|"
    r"Pier|Wharf|Quay)$"
)
PLACE_START = {"Mount", "Mt", "Lake", "Port", "Fort", "Cape", "Isle", "Gulf"}
HOLIDAYS = {
    h.strip()
    for h in (
        "christmas, easter, thanksgiving, halloween, ramadan, eid, eid al-fitr, eid ul-fitr, eid al-adha, eid ul-adha, "
        "diwali, hanukkah, chanukah, passover, hajj, holi, navratri, lent, advent, pentecost, kwanzaa, oktoberfest, "
        "coachella, olympics, brexit, hogmanay, nowruz, yom kippur, rosh hashanah, black friday, cyber monday, wwdc, ces, "
        "sxsw"
    ).split(",")
}
DAYS = {h.strip() for line in HOLIDAYS for h in re.split(r"\s{2,}|(?<=[a-z]) (?=[a-z])", line)} - {""}
DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
MONTHS = {
    "january",
    "february",
    "march",
    "april",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
# names that, on their own, are mostly something else: a person only as the first of several words ("Bill Gates")
AMBIGUOUS = set(
    """will mark bill grant hope faith joy art guy pat rich major king queen prince princess royal noble young summer
autumn dawn crystal amber ruby pearl ivy lily rose iris jade holly cherry ginger precious destiny charity sunny misty
windy rusty dusty penny sandy candy bud buddy buck chance chase hunter christian page porter walker ward reed lane clay
cole drew dean miles gay fern flora olive violet hazel heather sterling forest forrest brook brooks april may june
august ebony ivory jewel merry rocky sol wiley trinity unique santa santos cruz reyes mac del van von easter mercedes
chanel alexa claude tiffany lincoln harley mack sierra raven angel august china india asia kenya paris georgia
jordan""".split()
)
WORD = re.compile(r"[^\W\d_][\w'’-]*")


def _lines(name):
    return [s.strip() for s in (HERE / name).read_text(encoding="utf-8").splitlines() if s.strip() and not s.startswith("#")]


def key(name):
    """A name as the lists hold it: lower case, single spaces, no possessive, no leading "the"."""
    k = re.sub(r"\s+", " ", re.sub(r"[’']s$", "", str(name).strip()).replace("’", "'").casefold()).strip()
    return k[4:] if k.startswith("the ") else k


@functools.cache
def given_names():
    return frozenset(n.casefold() for n in _lines("given_names.txt"))


@functools.cache
def places():
    return frozenset(key(n) for n in _lines("places.txt"))


@functools.cache
def known():
    """{key: ORG or PRODUCT} for well-known organisations and products (lexicon/)."""
    return {**{key(n): "PRODUCT" for n in _lines("products.txt")}, **{key(n): "ORG" for n in _lines("organisations.txt")}}


def _base(t):
    return re.sub(r"[’']s$", "", t)


def _initial(t):
    return re.fullmatch(r"[A-Z]\.?", t) is not None


def kind(words, before="", after="", people=frozenset()):
    """The type of a name made of `words` (its tokens, as said), between `before` and `after` (the text around it);
    None when nothing tells. `people`: keys of names known to be people (a namespace's named speakers, and their first
    names)."""
    words = [_base(w) for w in words if w]
    if not words:
        return None
    n, first, last = len(words), words[0].rstrip("."), words[-1]
    k = key(" ".join(words))
    prev = [w.casefold() for w in WORD.findall(before[-60:])][-3:]
    nxt = next(iter(WORD.findall(after[:40])), "").casefold()
    named = first.casefold() in given_names()
    given = named and (n > 1 or first.casefold() not in AMBIGUOUS)
    if n > 1 and k in places():
        return "PLACE"
    if len(prev) >= 2 and prev[-1] in ("called", "named") and prev[-2] in CALLED:
        return CALLED[prev[-2]]
    if k in known():
        return known()[k]
    if k in people:
        return "PERSON"
    if n > 1:  # what a name of several words ends or starts with outranks a title in it (General Motors, King's College)
        if any(ORG_END.match(w) for w in words[1:]):
            return "ORG"
        if EVENT_END.match(last) or first in EVENT_START:
            return "EVENT"
        if WORK_END.match(last) or first in WORK_START:
            return "WORK"
        if PLACE_END.match(last) or first in PLACE_START:
            return "PLACE"
        if first in TITLES:
            return "PERSON"
    if prev and prev[-1] in ROLES:
        return "PERSON"
    if any(ch.isdigit() for w in words for ch in w) and not re.fullmatch(r"[A-Z0-9&]{2,8}", "".join(words)):
        return "PRODUCT"
    if named and nxt in SAYS:
        return "PERSON"
    if k in places():
        return "PLACE"
    if n > 1 and first in ORG_START:
        return "ORG"
    if k in HOLIDAYS:
        return "EVENT"
    if n == 1 and (k in MONTHS or (k.endswith("s") and k[:-1] in DAYS) or k in DAYS):
        return "DATE"
    if given or (n > 1 and _initial(words[0]) and not _initial(last)):
        return "PERSON"
    if 1 < n <= 3 and nxt in SAYS:
        return "PERSON"
    return None


def votes(name, lines=(), people=frozenset()):
    """({type: how many of the lines `name` is said in give it that type}, how many lines say it)."""
    words = WORD.findall(name) or [name]
    out, said = {}, 0
    for line in lines:
        text = line or ""
        m = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I)
        if not m:
            continue
        said += 1
        t = kind(words, text[: m.start()], text[m.end() :], people)
        if t:
            out[t] = out.get(t, 0) + 1
    return out, said


def best(counts):
    """The type most lines give, a person when it's a tie with one; None for no votes."""
    return max(counts, key=lambda t: (counts[t], t == "PERSON")) if counts else None


def guess(name, lines=(), people=frozenset()):
    """The type rules give `name` from what it is and the lines it's said in: the type most of them agree on, else
    None."""
    counts, said = votes(name, lines, people)
    if not said:
        return kind(WORD.findall(name) or [name], people=people)
    return best(counts)


# ---------- what the rules can't tell, a model may ----------
ASKED = ("PERSON", "ORG", "PRODUCT", "PLACE", "EVENT", "WORK", "TERM")  # the types a model picks from
TYPINGS = ("auto", "model", "rules")
BATCH = 20  # names in one call
MOST = 40  # names asked about at once (one recording, or one go of type_terms)
LINES = 2  # lines a name is said in, given with it


def _typing(cfg):
    return (cfg.get("analysis") or {}).get("typing") or "auto"


def asking(cfg):
    """Which model gives types to the names the rules leave terms (`analysis.typing`), or None: with "auto" (the
    default) the decision model when one is set up (Jev or Laya), with "model" the language model too when none is,
    with "rules" none. Decisions turned off (decisions.engine: off) is none either."""
    want = _typing(cfg)
    how = decide.engine(cfg)
    if want == "rules" or how is None:
        return None
    return how if how in decide.ENGINES or want == "model" else None


def said_in(name, lines, most=LINES):
    """The first `most` lines `name` is said in, each cut to a sentence's length."""
    pat = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.I)
    return [str(t)[:200] for t in lines if t and pat.search(str(t))][:most]


def ask(cfg, names):
    """{key: type} for names the rules left terms ({key: (name, [lines it's said in])}), from the model `asking` picks:
    only the types it's sure of (decisions.act_above) and not a term. Nothing when no model is set up, or it fails, or
    the budget's hard limit is reached: the names stay terms."""
    from .entity_setup import BUILTIN_HELP

    how = asking(cfg)
    if not how or not names:
        return {}
    options = {t: BUILTIN_HELP[t] for t in ASKED}
    keys = list(names)[:MOST]
    per = 1 if how == "laya" else BATCH  # Laya reads 512 or 1,024 tokens of state: one name at a time
    out = {}
    for i in range(0, len(keys), per):
        chunk = keys[i : i + per]
        state = {
            "about": "Names found in a text, with lines they're said in.",
            "names": [{"name": names[k][0], "said": list(names[k][1])[:LINES]} for k in chunk],
        }
        asks = {k: (f'What kind of thing is "{names[k][0]}"?', options) for k in chunk}
        try:
            got = decide.choose_all(cfg, asks, state, language_model=_typing(cfg) == "model")
        except Exception as e:  # noqa: BLE001 - the rules' types stay when the model can't answer
            log.warning("name types: the model couldn't be asked (%s); the names stay terms", e)
            break
        out.update({k: d["choice"] for k, d in got.items() if d["choice"] != "TERM" and decide.sure(cfg, d)})
    return out
