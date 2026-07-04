from knbs_scraper2 import KNBSExtractor

sample_pdf = "https://www.knbs.or.ke/wp-content/uploads/2026/06/Kenya-Leading-Economic-Indicators-April-2026.pdf"

extractor = KNBSExtractor()
results = extractor.get_cbr(sample_pdf)
print(results)