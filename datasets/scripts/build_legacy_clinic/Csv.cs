using System.Text;

namespace ClinicDesk;

/// <summary>Minimal RFC 4180 reader: quoted fields, doubled quotes, embedded commas and line breaks.</summary>
internal static class Csv
{
    public static List<Dictionary<string, string>> Read(string path)
    {
        var text = File.ReadAllText(path, new UTF8Encoding(false));
        if (text.Length > 0 && text[0] == '﻿') text = text[1..];

        var records = new List<List<string>>();
        var row = new List<string>();
        var field = new StringBuilder();
        bool quoted = false;
        for (int i = 0; i < text.Length; i++)
        {
            char c = text[i];
            if (quoted)
            {
                if (c == '"')
                {
                    if (i + 1 < text.Length && text[i + 1] == '"') { field.Append('"'); i++; }
                    else quoted = false;
                }
                else field.Append(c);
            }
            else if (c == '"') quoted = true;
            else if (c == ',') { row.Add(field.ToString()); field.Clear(); }
            else if (c == '\n' || c == '\r')
            {
                if (c == '\r' && i + 1 < text.Length && text[i + 1] == '\n') i++;
                row.Add(field.ToString()); field.Clear();
                records.Add(row); row = new List<string>();
            }
            else field.Append(c);
        }
        if (field.Length > 0 || row.Count > 0) { row.Add(field.ToString()); records.Add(row); }

        var header = records[0];
        var result = new List<Dictionary<string, string>>(records.Count - 1);
        for (int r = 1; r < records.Count; r++)
        {
            if (records[r].Count == 1 && records[r][0].Length == 0) continue;
            var map = new Dictionary<string, string>(header.Count);
            for (int c = 0; c < header.Count; c++) map[header[c]] = c < records[r].Count ? records[r][c] : "";
            result.Add(map);
        }
        return result;
    }
}
