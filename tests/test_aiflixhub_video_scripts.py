from __future__ import annotations

from scripts.import_aiflixhub_videos import parse_detail_video_url, parse_list_html


LIST_FIXTURE = """
<div class="pagination__next"></div>
<div class="grid-item fantasy ">
  <div class="card cardMovie" id="cardMovie525">
    <span id="sortContent" style="display:none;">
      <span class="date">12/07/2025</span>
      <span class="views">23</span>
      <span class="likes">1</span>
      <span class="duration">391</span>
    </span>
    <span id="searchContent" style="display:none;">
      Dragon Tamer A warrior saves a girl from a dragon&#039;s lair.
    </span>
    <img src="https://aiflixhub.com/media/cache/cover.png"
         data-original="/bundles/project/movies/cover.png" />
    <h5 class="text-light title-shadow">Dragon Tamer</h5>
    <a class="card-link btnDetail" href="/movie/dragon-tamer">Details</a>
  </div>
</div>
"""


def test_parse_list_html_extracts_movie_metadata_and_absolute_urls() -> None:
    rows = parse_list_html(LIST_FIXTURE, page=2)

    assert rows == [
        {
            "movie_id": 525,
            "page": 2,
            "category": "fantasy",
            "title": "Dragon Tamer",
            "description": "Dragon Tamer A warrior saves a girl from a dragon's lair.",
            "duration_seconds": 391,
            "date": "12/07/2025",
            "views": "23",
            "likes": "1",
            "detail_url": "https://aiflixhub.com/movie/dragon-tamer",
            "cover_url": "https://aiflixhub.com/bundles/project/movies/cover.png",
            "cover_fallback_url": "https://aiflixhub.com/media/cache/cover.png",
        }
    ]


def test_parse_detail_video_url_prefers_source_and_normalizes_site_double_slash() -> None:
    detail_url = "https://aiflixhub.com/movie/dragon-tamer"
    source_html = '<video><source src="/bundles/project/movies/movie.mp4" type="video/mp4"></video>'
    json_ld_html = '{"contentUrl": "https://aiflixhub.com//bundles/project/movies/movie.mp4"}'

    assert parse_detail_video_url(source_html, detail_url) == (
        "https://aiflixhub.com/bundles/project/movies/movie.mp4"
    )
    assert parse_detail_video_url(json_ld_html, detail_url) == (
        "https://aiflixhub.com/bundles/project/movies/movie.mp4"
    )
