from time import perf_counter

from reviewer import classify


sample = "זה נשמע מבאס, אבל אין סיבה לדאוג. אתה אמיץ, והכול יהיה בסדר."
count = 10000
start = perf_counter()
for _ in range(count):
    classify(sample)
elapsed = (perf_counter() - start) * 1000
print(f"classify: {count} iterations in {elapsed:.2f} ms ({elapsed / count:.4f} ms/call)")
