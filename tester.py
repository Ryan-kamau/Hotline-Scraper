from knbs_scraper import KNBSExtractor

sample_pdf = "https://www.knbs.or.ke/wp-content/uploads/2026/05/Leading-Economic-Indicators-March-2026.pdf"

extractor = KNBSExtractor()
results = extractor.get_exchange_rates(sample_pdf)
print(results)
result =  extractor.get_exchange_rates(sample_pdf)
print(result)