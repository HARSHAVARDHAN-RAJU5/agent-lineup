import io

from lineup import table


def test_box_table():
    out = table.draw(("ID", "File"), [[("1", "app.py")], [("2", "auth.py")]], (5, 20), table.BOX)
    assert out == (
        "┌────┬─────────┐\n"
        "│ ID │ File    │\n"
        "├────┼─────────┤\n"
        "│ 1  │ app.py  │\n"
        "├────┼─────────┤\n"
        "│ 2  │ auth.py │\n"
        "└────┴─────────┘\n"
    )


def test_plain_lines_when_the_terminal_cant_show_boxes():
    assert table.style(io.TextIOWrapper(io.BytesIO(), encoding="cp1252")) is table.PLAIN
    assert table.style(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")) is table.BOX
    out = table.draw(("ID",), [[("1",)]], (5,), table.PLAIN)
    assert out == "+----+\n| ID |\n+----+\n| 1  |\n+----+\n"


def test_long_cells_wrap_and_new_lines_split():
    out = table.draw(("Why",), [[("assertion removed\n+ possible weakened test, check this",)]], (20,), table.PLAIN)
    assert out.splitlines()[3:7] == [
        "| assertion removed   |",
        "| + possible weakened |",
        "|   test, check this  |",
        "+---------------------+",
    ]
