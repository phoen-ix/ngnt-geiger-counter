import tseslint from 'typescript-eslint'

// One rule: a Playwright call without its `await` runs on unawaited while the test goes on,
// and passes or fails by timing.
export default tseslint.config({
  files: ['**/*.ts'],
  ignores: ['node_modules/**'],
  languageOptions: {
    parser: tseslint.parser,
    parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
  },
  plugins: { '@typescript-eslint': tseslint.plugin },
  rules: { '@typescript-eslint/no-floating-promises': 'error' },
})
