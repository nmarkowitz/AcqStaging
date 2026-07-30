# Global Output Policy

Apply these output rules to every task and every repository.

## Repository exploration

Perform repository exploration silently.

Do not narrate or list:

- files read or opened
- directories inspected
- searches performed
- symbols or references examined
- commands used only to inspect the repository
- unchanged files

## File changes

Never display:

- patches
- diffs
- edited code
- before-and-after code
- line-by-line change summaries
- detailed descriptions of modifications

For every modified file, output exactly:

`<relative file path> has changed`

For example:

`src/App.tsx has changed`

For every newly created file, output exactly:

`<relative file path> has been created`

For every deleted file, output exactly:

`<relative file path> has been deleted`

Print one line per affected file.

Afterward, provide no additional explanation unless:

1. the task failed,
2. tests failed,
3. user action is required, or
4. the user explicitly requested an explanation or diff.

When reporting a failure, state only the concise error and required action.


# Explaining code

When explaining code use the @code-explainer-pro.toml skill

# Print messages

Make use of the Caveman skill unless the user otherwise specifies