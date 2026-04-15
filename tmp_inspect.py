from pathlib import Path
p = Path('main.py')
lines = p.read_text(encoding='utf-8').splitlines()
for i in range(600,680):
    print(f'{i+1}: {lines[i]}')
