import { Pipe, PipeTransform } from '@angular/core';
import { marked } from 'marked';

/**
 * Renders the advisor's GitHub-flavored markdown (headings, tables, lists) to
 * HTML for `[innerHTML]`, which Angular sanitizes — scripts/handlers are
 * stripped, while tables and formatting are kept. Pure, so it parses each
 * distinct answer once. Used only for assistant text, never raw user input.
 */
@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return marked.parse(value, { async: false, gfm: true, breaks: false }) as string;
  }
}
