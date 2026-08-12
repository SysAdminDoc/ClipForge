from clipforge.ocr import (
    OCRSegment,
    format_srt,
    merge_ocr_observations,
    output_srt_path,
    parse_tesseract_tsv,
)


def test_tesseract_tsv_groups_words_into_readable_lines():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\t"
        "top\twidth\theight\tconf\ttext\n"
        "5\t1\t2\t1\t1\t2\t0\t0\t10\t10\t91.2\tworld\n"
        "5\t1\t2\t1\t1\t1\t0\t0\t10\t10\t90.0\tHello\n"
        "5\t1\t2\t1\t2\t1\t0\t0\t10\t10\t12.0\tnoise\n"
    )
    assert parse_tesseract_tsv(tsv) == "Hello world"


def test_ocr_observations_merge_stable_text_and_bound_segments():
    segments = merge_ocr_observations(
        [(0.0, "Hello"), (0.5, "Hello"), (1.0, "World")],
        duration=2.0,
        sample_interval=0.5,
    )
    assert segments == [
        OCRSegment(0.0, 1.0, "Hello"),
        OCRSegment(1.0, 1.5, "World"),
    ]


def test_srt_format_is_subrip_and_destination_is_canonical():
    text = format_srt([OCRSegment(0.0, 1.234, "Hello\nworld")])
    assert "00:00:00,000 --> 00:00:01,234" in text
    assert "Hello world" in text
    assert output_srt_path("captions.txt").name == "captions.srt"
