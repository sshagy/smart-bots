import pytest

from src.config import DEFAULT_KEYWORDS_FILE, read_keywords_file
from src.domain.services import ModerationService, normalize_text

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def default_service() -> ModerationService:
    """Сервис со списком слов, который поставляется с проектом."""
    return ModerationService(read_keywords_file(DEFAULT_KEYWORDS_FILE))


async def test_whole_word_keyword_matches() -> None:
    match = ModerationService(["бля"]).find_violation("Ну бля, опять дождь")

    assert match is not None
    assert match.keyword == "бля"
    assert match.fragment == "бля"


@pytest.mark.parametrize("text", ["сто рублей и два рубля", "палуба корабля", "сабля"])
async def test_whole_word_keyword_does_not_match_inside_other_words(text: str) -> None:
    assert ModerationService(["бля"]).find_violation(text) is None


async def test_prefix_wildcard() -> None:
    service = ModerationService(["бляд*"])

    assert service.find_violation("это блядство").fragment == "блядство"
    assert service.find_violation("бляд").fragment == "бляд"  # «*» допускает пустой хвост
    assert service.find_violation("заблядовать") is None  # начало слова обязательно


async def test_infix_wildcard() -> None:
    match = ModerationService(["*пизд*"]).find_violation("вот распиздяй")

    assert match is not None
    assert match.fragment == "распиздяй"


async def test_case_and_yo_are_ignored() -> None:
    assert ModerationService(["ебать"]).find_violation("ЁБАТЬ") is not None


@pytest.mark.parametrize("text", ["xуй", "хyй", "6ля"])  # латинские x/y и цифра 6
async def test_latin_homoglyphs_and_digits_inside_cyrillic_words(text: str) -> None:
    assert ModerationService(["хуй", "бля"]).find_violation(text) is not None


async def test_pure_latin_words_are_not_transliterated() -> None:
    service = ModerationService(["хер"])

    assert service.find_violation("xep") is None  # чисто латинское слово не трогаем
    assert service.find_violation("хеp") is not None  # кириллица + латинская p


async def test_stretched_letters_and_invisible_characters() -> None:
    service = ModerationService(["бля"])

    assert service.find_violation("бляяяяя") is not None
    assert service.find_violation("б​ля") is not None


async def test_phrase_allows_any_whitespace() -> None:
    service = ModerationService(["иди на"])

    assert service.find_violation("иди   на\nработу") is not None
    assert service.find_violation("идина") is None


async def test_exceptions_suppress_matches() -> None:
    service = ModerationService(["*хуй*", "!*страху*"])

    assert service.find_violation("застрахуйте машину") is None
    match = service.find_violation("застрахуйте этот хуй")
    assert match is not None
    assert match.fragment == "хуй"


async def test_returns_first_violation_by_position() -> None:
    match = ModerationService(["бля", "сука"]).find_violation("сука, бля")

    assert match is not None
    assert match.keyword == "сука"


async def test_comments_blank_lines_and_duplicates_are_ignored() -> None:
    service = ModerationService(["# комментарий", "", "  бля  ", "бля"])

    assert service.keywords == ("бля",)


async def test_no_keywords_or_empty_text_means_no_violation() -> None:
    assert ModerationService([]).find_violation("бля") is None
    assert ModerationService(["бля"]).find_violation("") is None


@pytest.mark.parametrize("pattern", ["*", "* *", "!", "**"])
async def test_patterns_without_letters_are_rejected(pattern: str) -> None:
    with pytest.raises(ValueError, match="Некорректный шаблон"):
        ModerationService([pattern])


async def test_normalize_text() -> None:
    assert normalize_text("ПРИВЕТ, Ёжик! xуй 6ля") == "привет, ежик! хуй бля"


@pytest.mark.parametrize(
    "text",
    [
        "бля",
        "Блять, опять",
        "бляяяять",
        "на хуй",
        "иди нахуй",
        "охуеть",
        "хуёво",
        "пиздец",
        "Пи​здец",
        "заебал",
        "ЁБАНЫЙ",
        "уебан",
        "долбоёб",
        "блядство",
        "ну ты и сука",
        "fucking",
    ],
)
async def test_default_keywords_catch_profanity(
    default_service: ModerationService, text: str
) -> None:
    assert default_service.find_violation(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "рубля",
        "корабля",
        "сабля",
        "оскорблять",
        "употреблять",
        "истреблять",
        "ослаблять",
        "колебаться",
        "ребята",
        "хлебать",
        "хлебанул",
        "небо",
        "тебя",
        "страхуй",
        "застрахуйте",
        "страхует",
        "психуешь",
        "психуй",
        "мандарин",
        "команда",
        "сукно",
        "мудрый",
        "херувим",
        "шлюз",
        "Ебург",
        "бляха-муха",
        "чихуахуа",
        "психология",
        "ухудшение",
        "Привет! Как дела? Встречаемся в 19:00 у метро.",
    ],
)
async def test_default_keywords_have_no_false_positives(
    default_service: ModerationService, text: str
) -> None:
    assert default_service.find_violation(text) is None
