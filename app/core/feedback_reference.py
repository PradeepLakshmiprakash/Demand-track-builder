# ruff: noqa: E501  (the standard sentences are kept one to a line, as in the source)
"""The interview feedback form's reference data, as in Acquisition Central.

Six areas, each rated 1 to 10 and given a band; choosing a band puts its standard sentence into the
record, so interviewers never retype it. The sentences are the ones Acquisition Central carries (from the
interview feedback workbook), spelling slips included: changing them is a decision for whoever owns the
feedback template.

An account can name its own areas in Account settings; an area that isn't one of these six gets the
generic bands.
"""

PARAMETERS: dict[str, list[tuple[str, str]]] = {
    "Business Acumen/Problem Solving Skills": [
        ("Excellent", "(Excellent) Candidate has excellent business acumen and was able to discuss strong problem-solving skills in detail."),
        ("Good", "(Good) Candidate has good business acumen and was able to discuss some problem-solving skills."),
        ("Average", "(Average) Candidate has average business acumen and seems to have basic problem-solving skills."),
        ("Poor", "(Poor) Candidate has poor business acumen and was not able to talk about any relevant problem-solving skills."),
    ],
    "Technical Skills": [
        ("Excellent", "(Excellent) Candidate has multiple advance technical skills and was able to answer all my questions in detail and has done much work in the field."),
        ("Good", "(Good) Candidate has strong technical skills and was able to answer all my questions."),
        ("Average", "(Average) Candidate has average technical skills and was able to answer most of my questions."),
        ("Poor", "(Poor) Candidate has poor Technical skills and was not able to answer most of my questions."),
        ("Basic", "(Basic) Candidate has basic broad-spectrum technical knowledge."),
    ],
    "Domain Knowledge": [
        ("Excellent", "(Excellent) Candidate is a financial services subject matter expert and has multiple relevant work experiences in the financial services companies"),
        ("Good", "(Good) Candidate has strong financial services knowledge and as passed experience in the field."),
        ("Average", "(Average) Candidate has average financial services knowledge and understands the field."),
        ("Poor", "(Poor) Candidate has poor financial services knowledge and is not aware of the field."),
    ],
    "Communication": [
        ("Excellent", "(Excellent) Candidate has excellent communication skills and was able to hold the conversation in a clear and concise way."),
        ("Good", "(Good) Candidate has strong communication skills and was able to hold the conversation with no issue."),
        ("Average", "(Average) Candidate has average communication skills was able to mostly hold to conversation with only little issue."),
        ("Poor", "(Poor) Candidate has poor communication skills and wad not able to hold the conversation in a reasonable way."),
    ],
    "Client Interfacing": [
        ("Excellent", "(Excellent) Candidate has excellent client facing skills and was put together and confident during the entire interview. They would be an excellent Capgemini ambassador."),
        ("Good", "(Good) Candidate has strong client facing skills and would be a good Capgemini ambassador."),
        ("Average", "(Average) Candidate has average client facing skills and should not be a problem being a Capgemini ambassador."),
        ("Poor", "(Poor) Candidate has poor client facing skills and could cause issues working with clients."),
    ],
    "Project/Capgemini Fit": [
        ("Good", "(Good) Candidate has strong values and would be and good fit for Capgemini"),
        ("Poor", "(Poor) Candidate does not share Capgemini values and would be a poor fit for Capgemini"),
    ],
}  # fmt: skip

DEFAULT_AREAS = list(PARAMETERS)

GENERIC_BANDS = [
    ("Excellent", "(Excellent) The candidate was excellent in this area."),
    ("Good", "(Good) The candidate was good in this area."),
    ("Average", "(Average) The candidate was average in this area."),
    ("Poor", "(Poor) The candidate was poor in this area."),
]

# The usual rating for each band. Any combination is allowed; the form only points out an unusual one.
BAND_RANGES: dict[str, tuple[int, int]] = {
    "Excellent": (9, 10),
    "Good": (7, 8),
    "Average": (5, 6),
    "Basic": (3, 6),
    "Poor": (1, 4),
}

SUPPORT_OPTIONS = ["Candidate can be re-interviewed after improving in the following fields", "NA"]
SCALE = range(1, 11)


def bands_for(area: str) -> list[tuple[str, str]]:
    return PARAMETERS.get(area, GENERIC_BANDS)


def sentence(area: str, band: str) -> str | None:
    return dict(bands_for(area)).get(band)


def overall(ratings: list[int]) -> float | None:
    """The average of the ratings, to one decimal place."""
    return round(sum(ratings) / len(ratings), 1) if ratings else None


def form_reference(areas: list[str]) -> list[dict[str, object]]:
    """What the form needs for each area: its bands, sentences and usual ranges."""
    return [
        {
            "label": a,
            "bands": [
                {"band": b, "sentence": s, "range": list(BAND_RANGES.get(b, (1, 10)))}
                for b, s in bands_for(a)
            ],
        }
        for a in areas
    ]
