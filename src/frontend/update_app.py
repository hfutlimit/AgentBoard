import re

with open('src/app/app.ts', 'r', encoding='utf-8') as f:
    c = f.read()

# 1. Add ThemeService import
if 'ThemeService' not in c:
    c = c.replace('import { ApiService', 'import { ThemeService } from \'./core/services/theme\';\nimport { ApiService')

# 2. Inject ThemeService in constructor
if 'public readonly themeService: ThemeService' not in c:
    c = c.replace(
        '@Inject(DOCUMENT) private readonly document: Document,',
        '@Inject(DOCUMENT) private readonly document: Document,\n    public readonly themeService: ThemeService,'
    )

# 3. Remove old colorScheme logic
c = re.sub(r'  private readonly colorScheme = window\.matchMedia.*?\n', '', c)
c = re.sub(r'  private readonly handleColorSchemeChange = \(event: MediaQueryListEvent\): void => \{.*?\n  \};\n', '', c, flags=re.DOTALL)

# 4. Remove ngOnInit logic
c = re.sub(r"    const saved = localStorage\.getItem\('agentboard_theme'\);\n    // 优先使用用户偏好，其次跟随系统\n    const theme = saved \|\| \(this\.colorScheme\?\.matches \? 'dark' : 'light'\);\n    this\.applyTheme\(theme\);\n", '', c)
c = re.sub(r"    // Listen for system theme changes\n    this\.colorScheme\?\.addEventListener\('change', this\.handleColorSchemeChange\);\n", '', c)

# 5. Remove onDestroy logic
c = re.sub(r"    this\.colorScheme\?\.removeEventListener\('change', this\.handleColorSchemeChange\);\n", '', c)

# 6. Update toggleTheme
c = re.sub(
    r'  toggleTheme\(\): void \{.*?\n  \}',
    '  toggleTheme(): void {\n    this.themeService.toggleTheme();\n    this.notify(\n      this.isDarkTheme() ? \'已切换到深色模式 🌙\' : \'已切换到浅色模式 ☀️\'\n    );\n  }',
    c, flags=re.DOTALL
)

# 7. Update isDarkTheme
c = re.sub(
    r'  isDarkTheme\(\): boolean \{.*?\n  \}',
    '  isDarkTheme(): boolean {\n    return this.themeService.currentTheme() === \'dark\';\n  }',
    c, flags=re.DOTALL
)

# 8. Remove applyTheme
c = re.sub(r'  private applyTheme\(theme: string\): void \{.*?\n  \}\n', '', c, flags=re.DOTALL)

with open('src/app/app.ts', 'w', encoding='utf-8') as f:
    f.write(c)

print("Updated app.ts successfully")
