from tqs2etabs.importers.tqs.common import (is_pair, logical_lines, parse_pair, tokenize,
                                             unquote)


def test_comments_and_blank_lines_are_dropped():
    text = "$ comentario\n\n  DEFINE NLISTA   $ inline\n"
    lines = logical_lines(text)
    assert [l.text for l in lines] == ["DEFINE NLISTA"]
    assert lines[0].number == 3


def test_continuation_joins_lines_and_keeps_first_number():
    text = "  V18 EIXO 5P6 ARE 8RV9 50 -\n        N 9AV8\n  V19 EIXO 17AV7 ARD 19P3"
    lines = logical_lines(text)
    assert len(lines) == 2
    assert lines[0].text == "V18 EIXO 5P6 ARE 8RV9 50 N 9AV8"
    assert lines[0].number == 1
    assert lines[1].number == 3


def test_negative_number_at_end_is_not_a_continuation():
    text = "  V1 S1 18.0/120.0 DFS -40.00 S2 18.0/120.0 DFS -40.00 -\n  VOL 1 2\n  X -1"
    lines = logical_lines(text)
    assert len(lines) == 2
    assert lines[1].text == "X -1"


def test_dollar_inside_quotes_is_not_a_comment():
    lines = logical_lines("  'A $ B' 1 2")
    assert lines[0].text == "'A $ B' 1 2"


def test_tokenize_pairs_quotes_and_semicolons():
    toks = tokenize("  P3 G -434.746213,2214.468018; -434.746213,   2493.448749; BASE -464.7,2214.3 FCK 'C60'")
    assert toks[:2] == ["P3", "G"]
    assert toks[2] == "-434.746213,2214.468018"
    assert toks[3] == ";"
    assert toks[4] == "-434.746213,2493.448749"
    assert "FCK" in toks and toks[-1] == "'C60'"


def test_tokenize_val_glued_to_semicolon():
    toks = tokenize("L5 ARE -46.849,1527.454;   113.151,1527.454;VAL   0.10")
    assert toks[-2:] == ["VAL", "0.10"]


def test_tokenize_quoted_string_with_spaces():
    toks = tokenize("   'W 150 x 13.0' 0.1 0.2")
    assert toks[0] == "'W 150 x 13.0'"
    assert unquote(toks[0]) == "W 150 x 13.0"


def test_pair_helpers():
    assert is_pair("-1.5,2")
    assert not is_pair("1.5")
    assert parse_pair("-1.5,2") == (-1.5, 2.0)
