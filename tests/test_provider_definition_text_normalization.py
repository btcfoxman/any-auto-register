from infrastructure.provider_definitions_repository import _normalize_seed_text


def _windows_mojibake(value: str) -> str:
    chars = []
    for byte in value.encode("utf-8"):
        try:
            chars.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            chars.append(chr(byte))
    return "".join(chars)


def test_provider_seed_text_normalizes_mojibake():
    description = "HeroSMS \u63a5\u7801\u5e73\u53f0\uff0c\u652f\u6301\u53f7\u7801\u590d\u7528\u548c\u81ea\u52a8\u91cd\u53d1"
    hint = (
        "\u53d6\u7801\u5fc5\u987b\u6309\u77ed\u4fe1\u5173\u952e\u8bcd\u8fc7\u6ee4"
        "\uff1bLingYaQQ \u53ef\u586b\u201c\u817e\u8baf\u201d"
    )
    seed = {
        "description": _windows_mojibake(description),
        "fields": [
            {
                "label": _windows_mojibake("\u9ed8\u8ba4\u56fd\u5bb6"),
                "placeholder": _windows_mojibake("\u8bf7\u9009\u62e9\u56fd\u5bb6..."),
            },
            {
                "hint": _windows_mojibake(hint),
            },
        ],
    }

    result = _normalize_seed_text(seed)

    assert result["description"] == description
    assert result["fields"][0]["label"] == "\u9ed8\u8ba4\u56fd\u5bb6"
    assert result["fields"][0]["placeholder"] == "\u8bf7\u9009\u62e9\u56fd\u5bb6..."
    assert result["fields"][1]["hint"] == hint
