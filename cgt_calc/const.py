from typing import Dict, Final

# Allowances
# https://www.gov.uk/guidance/capital-gains-tax-rates-and-allowances#tax-free-allowances-for-capital-gains-tax
CAPITAL_GAIN_ALLOWANCES: Final[Dict[int, int]] = {
    2014: 11000,
    2015: 11100,
    2016: 11100,
    2017: 11300,
    2018: 11700,
    2019: 12000,
    2020: 12300,
}
