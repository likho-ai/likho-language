# likho-hinglish

Devanagari (Hindi/Urdu) to Hinglish, the Roman-letter Hindi people type in chat. Pure Python,
no dependencies.

```python
from likho_hinglish import Transliterator, to_hinglish

to_hinglish("नमस्ते, आपका ऑर्डर कल तक पहुँच जाएगा")
# 'namaste, aapka order kal tak pahunch jayega'

Transliterator({"त्रिफला": "Triphala"}).text("त्रिफला लीजिए")
# 'Triphala lijiye'
```

A word is written with, in this order: the caller's own spelling, the built-in table
(`words.py`), the rules (`rules.py`). A spelling whose source has spaces is a phrase and is
replaced as a whole. Latin text, digits and punctuation pass through.

Install from the likho-language repository:

```toml
dependencies = ["likho-hinglish"]

[tool.uv.sources]
likho-hinglish = { git = "https://github.com/likho-ai/likho-language", tag = "v0.1.0", subdirectory = "packages/likho-hinglish" }
```
