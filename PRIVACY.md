# Privacy Policy

**Lemniscus** is a local-first Apple Health MCP server. This policy explains how your data is handled.

## Data storage

- All health data is stored **locally on your machine** in a SQLite database inside the data folder you configure during installation.
- No data is uploaded to Lemniscus servers — there are no Lemniscus servers.
- No telemetry, analytics, or usage tracking of any kind is collected.

## Data processing

- Your Apple Health `export.xml` is parsed and indexed entirely on your machine.
- The MCP server runs locally and communicates with Claude via stdio (standard input/output).
- No network connections are made by Lemniscus at any time.

## Data shared with Claude

- When you ask Claude a question, Claude calls Lemniscus tools to retrieve relevant health data. That data is then sent to Anthropic's API as part of your conversation.
- This data transmission is governed by **Anthropic's privacy policy**: [anthropic.com/privacy](https://www.anthropic.com/privacy)
- Lemniscus itself does not send data to Anthropic — Claude does, as part of normal MCP tool use.

## Data deletion

- To delete all indexed data, remove the SQLite database file (`lemniscus.db`) and the `.indexed_files.json` manifest from your data folder.
- To remove Lemniscus entirely, delete the extension or the cloned repository. No data is stored outside your configured data folder.

## Third-party services

- Lemniscus has **no dependencies on third-party services**. It makes zero network requests.
- The only external interaction is Claude's normal API communication with Anthropic, which is outside of Lemniscus's control.

## Medical disclaimer

Lemniscus is not a medical device, diagnostic tool, or clinical product. It has not been evaluated, approved, or cleared by the FDA or any other regulatory authority. LLM outputs may be inaccurate, incomplete, or misleading. Do not use this software to make medical decisions. Always consult a qualified healthcare professional for medical advice, diagnosis, or treatment.

## Contact

For questions about this privacy policy, open an issue at [github.com/cjgoodmaker/lemniscus_open](https://github.com/cjgoodmaker/lemniscus_open/issues).

---

*Last updated: March 2026*
