using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Linq;
using System.Threading.Tasks;
using System.Threading;
using Trace.Api.Common.Ituff;
using Trace.Api.Services.TestResults.ItuffIndex;
using Trace.Api.Common;
using Trace.Api.Services.BinSwitch;
using Trace.Api.Services.BinSwitch.Interfaces;
using Trace.Api.Services.Cache;

namespace TraceBridge;

class Program
{
    static int Main(string[] args)
    {
        if (args.Length == 0 || args[0] == "--help" || args[0] == "-h")
        {
            PrintUsage();
            return 0;
        }

        string command = args[0].ToLower();

        try
        {
            switch (command)
            {
                case "search":
                    return CmdSearch(args[1..]);
                case "get-ituffs":
                    return CmdGetItuffs(args[1..]);
                case "get-by-program":
                    return CmdGetByProgram(args[1..]);
                case "get-by-visualid":
                    return CmdGetByVisualId(args[1..]);
                case "bin-dist":
                    return CmdBinDist(args[1..]);
                case "xeus-get":
                    return CmdXeusGet(args[1..]);
                case "xeus-bin-dist":
                    return CmdXeusBinDist(args[1..]);
                case "xeus-units":
                    return CmdXeusUnits(args[1..]);
                default:
                    Console.Error.WriteLine($"Unknown command: {command}");
                    PrintUsage();
                    return 1;
            }
        }
        catch (Exception ex)
        {
            var err = new { error = ex.GetType().Name, message = ex.Message };
            Console.WriteLine(JsonSerializer.Serialize(err));
            return 1;
        }
    }

    // -------------------------------------------------------------------------
    // search <query>
    //   Search the worldwide ituff index (SORT) for a lot, visual ID, etc.
    //   --type sort|class   (default: sort)
    // -------------------------------------------------------------------------
    static int CmdSearch(string[] args)
    {
        string query = "";
        string type = "sort";
        string siteName = "FM";
        string sortSourceName = "AMR";
        string siteDataSourceName = "CLASS";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--type" && i + 1 < args.Length)
                type = args[++i].ToLower();
            else if (args[i] == "--site" && i + 1 < args.Length)
                siteName = args[++i].ToUpperInvariant();
            else if (args[i] == "--sort-source" && i + 1 < args.Length)
                sortSourceName = args[++i].ToUpperInvariant();
            else if (args[i] == "--site-datasource" && i + 1 < args.Length)
                siteDataSourceName = args[++i].ToUpperInvariant();
            else if (!args[i].StartsWith("--"))
                query = args[i];
        }

        if (string.IsNullOrEmpty(query))
        {
            Console.Error.WriteLine("search: missing <query>");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        IEnumerable<ItuffDefinition> results = Array.Empty<ItuffDefinition>();

        if (type == "class")
        {
            if (!Enum.TryParse(siteName, true, out SiteEnum site))
            {
                Console.Error.WriteLine($"Invalid --site '{siteName}'. Example: FM, IDC, SC");
                return 1;
            }

            if (!Enum.TryParse(siteDataSourceName, true, out SiteDataSourceEnum siteDataSource))
            {
                Console.Error.WriteLine($"Invalid --site-datasource '{siteDataSourceName}'. Example: CLASS, CLASSHDMT, PPV");
                return 1;
            }

            var mgr = new ItuffIndexManager(site, siteDataSource, TaskScheduler.Default, null!);
            results = mgr.Search(query, cts.Token).GetAwaiter().GetResult();
        }
        else
        {
            if (!Enum.TryParse(sortSourceName, true, out SortDataSourceEnum source))
            {
                Console.Error.WriteLine($"Invalid --sort-source '{sortSourceName}'. Example: AMR, GER, GAR");
                return 1;
            }

            var mgr = new SortItuffIndexManager(source, TaskScheduler.Default, null!);
            results = mgr.Search(query, cts.Token).GetAwaiter().GetResult();
        }

        var output = new List<object>();
        foreach (var d in results)
            output.Add(ItuffDefToDict(d));

        Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }

    // -------------------------------------------------------------------------
    // get-ituffs --lot <lot> --operation <op>
    //   Fetch ituff definitions from Aries by lot + operation.
    // -------------------------------------------------------------------------
    static int CmdGetItuffs(string[] args)
    {
        string lot = "";
        string operation = "";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--lot" && i + 1 < args.Length)
                lot = args[++i];
            else if (args[i] == "--operation" && i + 1 < args.Length)
                operation = args[++i];
        }

        if (string.IsNullOrEmpty(lot))
        {
            Console.Error.WriteLine("get-ituffs: --lot is required");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        var mgr = new AriesManager(null!);
        var defs = mgr.GetItuffDefinitions(lot, operation, cts.Token).GetAwaiter().GetResult();

        var output = new List<object>();
        foreach (var d in defs)
            output.Add(ItuffDefToDict(d));

        Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }

    // -------------------------------------------------------------------------
    // get-by-program --program <programName>
    // -------------------------------------------------------------------------
    static int CmdGetByProgram(string[] args)
    {
        string programName = "";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--program" && i + 1 < args.Length)
                programName = args[++i];
        }

        if (string.IsNullOrEmpty(programName))
        {
            Console.Error.WriteLine("get-by-program: --program is required");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        var mgr = new AriesManager(null!);
        var defs = mgr.GetItuffDefinitionsByProgramName(programName, cts.Token).GetAwaiter().GetResult();

        var output = new List<object>();
        foreach (var d in defs)
            output.Add(ItuffDefToDict(d));

        Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }

    // -------------------------------------------------------------------------
    // get-by-visualid --visualid <visualId>
    // -------------------------------------------------------------------------
    static int CmdGetByVisualId(string[] args)
    {
        string visualId = "";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--visualid" && i + 1 < args.Length)
                visualId = args[++i];
        }

        if (string.IsNullOrEmpty(visualId))
        {
            Console.Error.WriteLine("get-by-visualid: --visualid is required");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        var mgr = new AriesManager(null!);
        var defs = mgr.GetItuffDefinitionsByVisualId(visualId, cts.Token).GetAwaiter().GetResult();

        var output = new List<object>();
        foreach (var d in defs)
            output.Add(ItuffDefToDict(d));

        Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }

    // -------------------------------------------------------------------------
    // bin-dist --lot <lot> [--operation <op>] [--program <program>]
    //          [--site <site>] [--site-datasource CLASS|CLASSHDMT]
    //          [--bin-kind interface|hard|functional|full]
    // -------------------------------------------------------------------------
    static int CmdBinDist(string[] args)
    {
        string lot = "";
        string operation = "";
        string program = "";
        string siteName = "JF";
        string siteDataSourceName = "CLASSHDMT";
        string binKind = "interface";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--lot" && i + 1 < args.Length)
                lot = args[++i];
            else if (args[i] == "--operation" && i + 1 < args.Length)
                operation = args[++i];
            else if (args[i] == "--program" && i + 1 < args.Length)
                program = args[++i];
            else if (args[i] == "--site" && i + 1 < args.Length)
                siteName = args[++i].ToUpperInvariant();
            else if (args[i] == "--site-datasource" && i + 1 < args.Length)
                siteDataSourceName = args[++i].ToUpperInvariant();
            else if (args[i] == "--bin-kind" && i + 1 < args.Length)
                binKind = args[++i].ToLowerInvariant();
        }

        if (string.IsNullOrWhiteSpace(lot) && string.IsNullOrWhiteSpace(program))
        {
            Console.Error.WriteLine("bin-dist: provide --lot and/or --program");
            return 1;
        }

        if (!Enum.TryParse(siteName, true, out SiteEnum site))
        {
            Console.Error.WriteLine($"Invalid --site '{siteName}'. Example: JF, FM, IDC");
            return 1;
        }

        if (!Enum.TryParse(siteDataSourceName, true, out SiteDataSourceEnum siteDataSource))
        {
            Console.Error.WriteLine($"Invalid --site-datasource '{siteDataSourceName}'. Example: CLASS, CLASSHDMT");
            return 1;
        }

        var allowedBinKinds = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "interface", "hard", "functional", "full"
        };
        if (!allowedBinKinds.Contains(binKind))
        {
            Console.Error.WriteLine($"Invalid --bin-kind '{binKind}'. Use: interface|hard|functional|full");
            return 1;
        }

        using var ituffIndexManager = new ItuffIndexManager(site, siteDataSource, TaskScheduler.Default, null!);
        var allItuffs = ituffIndexManager.GetAllItuffDefinitions();

        var filtered = allItuffs
            .Where(i => string.IsNullOrWhiteSpace(lot) || string.Equals(i.Lot ?? "", lot, StringComparison.OrdinalIgnoreCase))
            .Where(i => string.IsNullOrWhiteSpace(operation) || string.Equals(i.Operation ?? "", operation, StringComparison.OrdinalIgnoreCase))
            .Where(i => string.IsNullOrWhiteSpace(program)
                || (!string.IsNullOrWhiteSpace(i.ProgramName) && i.ProgramName.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0)
                || (!string.IsNullOrWhiteSpace(i.Name) && i.Name.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0))
            .OrderByDescending(i => i.EndDate)
            .ToList();

        if (filtered.Count == 0)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                message = "No matching ituff definitions found",
                lot,
                operation,
                program,
                site = site.ToString(),
                siteDataSource = siteDataSource.ToString(),
                binKind
            }, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }

        var selected = filtered[0];
        var unitsIndexManager = new UnitsIndexManager(site, siteDataSource);
        var units = unitsIndexManager.GetUnits(selected)?.ToList() ?? new List<ItuffUnit>();

        int GetBinNumber(ItuffUnit u)
        {
            if (u?.Bin == null)
                return -1;
            return binKind switch
            {
                "functional" => u.Bin.FunctionalBin,
                "full" => u.Bin.FullBin,
                "hard" => u.Bin.HardBin,
                _ => u.Bin.HardBin, // interface alias
            };
        }

        var totalUnits = units.Count;
        var dist = units
            .GroupBy(GetBinNumber)
            .Select(g => new
            {
                bin = g.Key,
                count = g.Count(),
                percent = totalUnits == 0 ? 0.0 : Math.Round(100.0 * g.Count() / totalUnits, 3)
            })
            .OrderByDescending(x => x.count)
            .ThenBy(x => x.bin)
            .ToList();

        Console.WriteLine(JsonSerializer.Serialize(new
        {
            selectedItuff = ItuffDefToDict(selected),
            query = new
            {
                lot,
                operation,
                program,
                site = site.ToString(),
                siteDataSource = siteDataSource.ToString(),
                binKind,
                matches = filtered.Count
            },
            totalUnits,
            distribution = dist
        }, new JsonSerializerOptions { WriteIndented = true }));

        return 0;
    }

    // -------------------------------------------------------------------------
    // xeus-get --lot <lot> [--operation <op>] [--program <program>] [--visualid <visualId>]
    //   Fetch ituff definitions from XEUS backend by lot.
    // -------------------------------------------------------------------------
    static int CmdXeusGet(string[] args)
    {
        string lot = "";
        string operation = "";
        string program = "";
        string visualId = "";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--lot" && i + 1 < args.Length)
                lot = args[++i];
            else if (args[i] == "--operation" && i + 1 < args.Length)
                operation = args[++i];
            else if (args[i] == "--program" && i + 1 < args.Length)
                program = args[++i];
            else if (args[i] == "--visualid" && i + 1 < args.Length)
                visualId = args[++i];
        }

        if (string.IsNullOrEmpty(lot))
        {
            Console.Error.WriteLine("xeus-get: --lot is required");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        var mgr = new XeusManager();
        var defs = mgr.GetItuffDefinitionsWithResolvedTp(cts.Token, lot).ToList();

        // Filter by operation if provided
        if (!string.IsNullOrEmpty(operation))
        {
            defs = defs.Where(d => string.Equals(d.Operation ?? "", operation, StringComparison.OrdinalIgnoreCase)).ToList();
        }

        // Filter by program if provided
        if (!string.IsNullOrEmpty(program))
        {
            defs = defs.Where(d =>
                (!string.IsNullOrWhiteSpace(d.ProgramName) && d.ProgramName.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0)
                || (!string.IsNullOrWhiteSpace(d.Name) && d.Name.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();
        }

        // Filter by visualId if provided
        if (!string.IsNullOrEmpty(visualId))
        {
            defs = defs.Where(d => string.Equals(d.Name ?? "", visualId, StringComparison.OrdinalIgnoreCase)).ToList();
        }

        var output = new List<object>();
        foreach (var d in defs)
            output.Add(ItuffDefToDict(d));

        Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }

    // -------------------------------------------------------------------------
    // xeus-bin-dist --lot <lot> [--operation <op>] [--program <program>]
    //               [--bin-kind interface|hard|functional|full]
    //   Compute bin distribution from units of the latest matching XEUS ituff.
    // -------------------------------------------------------------------------
    static int CmdXeusBinDist(string[] args)
    {
        string lot = "";
        string operation = "";
        string program = "";
        string binKind = "interface";

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--lot" && i + 1 < args.Length)
                lot = args[++i];
            else if (args[i] == "--operation" && i + 1 < args.Length)
                operation = args[++i];
            else if (args[i] == "--program" && i + 1 < args.Length)
                program = args[++i];
            else if (args[i] == "--bin-kind" && i + 1 < args.Length)
                binKind = args[++i].ToLowerInvariant();
        }

        if (string.IsNullOrWhiteSpace(lot))
        {
            Console.Error.WriteLine("xeus-bin-dist: --lot is required");
            return 1;
        }

        var allowedBinKinds = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "interface", "hard", "functional", "full"
        };
        if (!allowedBinKinds.Contains(binKind))
        {
            Console.Error.WriteLine($"Invalid --bin-kind '{binKind}'. Use: interface|hard|functional|full");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
        var mgr = new XeusManager();
        var defs = mgr.GetItuffDefinitionsWithResolvedTp(cts.Token, lot).ToList();

        // Filter by operation if provided
        if (!string.IsNullOrEmpty(operation))
        {
            defs = defs.Where(d => string.Equals(d.Operation ?? "", operation, StringComparison.OrdinalIgnoreCase)).ToList();
        }

        // Filter by program if provided
        if (!string.IsNullOrEmpty(program))
        {
            defs = defs.Where(d =>
                (!string.IsNullOrWhiteSpace(d.ProgramName) && d.ProgramName.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0)
                || (!string.IsNullOrWhiteSpace(d.Name) && d.Name.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();
        }

        if (defs.Count == 0)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                message = "No matching ituff definitions found in XEUS",
                lot,
                operation,
                program,
                binKind
            }, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }

        var ordered = defs.OrderByDescending(d => d.EndDate).ToList();
        var selected = ordered[0];

        var sessionFactory = new SessionFactory(new PassThroughFileService());
        using (var session = sessionFactory.CreateSession(selected))
        {
            session.SessionStartup.Wait();

            var bins = session.Units
                .Select(u => binKind switch
                {
                    "functional" => u.ItuffUnit.Bin.FunctionalBin,
                    "full" => u.ItuffUnit.Bin.FullBin,
                    "hard" => u.ItuffUnit.Bin.HardBin,
                    _ => u.ItuffUnit.Bin.HardBin, // interface alias
                })
                .ToList();

            var totalUnits = bins.Count;
            var dist = bins
                .GroupBy(b => b)
                .Select(g => new
                {
                    bin = g.Key,
                    count = g.Count(),
                    percent = totalUnits == 0 ? 0.0 : Math.Round(100.0 * g.Count() / totalUnits, 3)
                })
                .OrderByDescending(x => x.count)
                .ThenBy(x => x.bin)
                .ToList();

            Console.WriteLine(JsonSerializer.Serialize(new
            {
                selectedItuff = ItuffDefToDict(selected),
                query = new
                {
                    lot,
                    operation,
                    program,
                    binKind,
                    matches = ordered.Count,
                    backend = "XEUS"
                },
                totalUnits,
                distribution = dist,
                allMatches = ordered.Select(d => ItuffDefToDict(d)).ToList()
            }, new JsonSerializerOptions { WriteIndented = true }));
        }

        return 0;
    }

    // -------------------------------------------------------------------------
    // xeus-units --lot <lot> [--operation <op>] [--program <program>]
    //            [--bin-kind interface|hard|functional|full]
    //            [--bin <num>] [--wafer <wafer>] [--include-test true|false]
    //   Return per-unit rows from XEUS session for the latest matching wafer.
    // -------------------------------------------------------------------------
    static int CmdXeusUnits(string[] args)
    {
        string lot = "";
        string operation = "";
        string program = "";
        string wafer = "";
        string binKind = "interface";
        int? targetBin = null;
        bool includeTest = true;

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--lot" && i + 1 < args.Length)
                lot = args[++i];
            else if (args[i] == "--operation" && i + 1 < args.Length)
                operation = args[++i];
            else if (args[i] == "--program" && i + 1 < args.Length)
                program = args[++i];
            else if (args[i] == "--wafer" && i + 1 < args.Length)
                wafer = args[++i];
            else if (args[i] == "--bin-kind" && i + 1 < args.Length)
                binKind = args[++i].ToLowerInvariant();
            else if (args[i] == "--bin" && i + 1 < args.Length)
            {
                if (int.TryParse(args[++i], out var b))
                    targetBin = b;
                else
                {
                    Console.Error.WriteLine("xeus-units: --bin must be an integer");
                    return 1;
                }
            }
            else if (args[i] == "--include-test" && i + 1 < args.Length)
            {
                var val = args[++i];
                includeTest = !string.Equals(val, "false", StringComparison.OrdinalIgnoreCase);
            }
        }

        if (string.IsNullOrWhiteSpace(lot))
        {
            Console.Error.WriteLine("xeus-units: --lot is required");
            return 1;
        }

        var allowedBinKinds = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "interface", "hard", "functional", "full"
        };
        if (!allowedBinKinds.Contains(binKind))
        {
            Console.Error.WriteLine($"Invalid --bin-kind '{binKind}'. Use: interface|hard|functional|full");
            return 1;
        }

        var cts = new CancellationTokenSource(TimeSpan.FromSeconds(300));
        var mgr = new XeusManager();
        var defs = mgr.GetItuffDefinitionsWithResolvedTp(cts.Token, lot).ToList();

        if (!string.IsNullOrEmpty(operation))
            defs = defs.Where(d => string.Equals(d.Operation ?? "", operation, StringComparison.OrdinalIgnoreCase)).ToList();

        if (!string.IsNullOrEmpty(program))
        {
            defs = defs.Where(d =>
                (!string.IsNullOrWhiteSpace(d.ProgramName) && d.ProgramName.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0)
                || (!string.IsNullOrWhiteSpace(d.Name) && d.Name.IndexOf(program, StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();
        }

        if (!string.IsNullOrEmpty(wafer))
        {
            defs = defs.Where(d => string.Equals(d.Wafer ?? "", wafer, StringComparison.OrdinalIgnoreCase)
                                || (!string.IsNullOrWhiteSpace(d.Name) && d.Name.IndexOf($"W{wafer}_", StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();
        }

        if (defs.Count == 0)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                message = "No matching ituff definitions found in XEUS",
                lot,
                operation,
                program,
                wafer,
                binKind,
                targetBin
            }, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }

        var selected = defs.OrderByDescending(d => d.EndDate).First();
        var sessionFactory = new SessionFactory(new PassThroughFileService());
        using (var session = sessionFactory.CreateSession(selected))
        {
            if (!session.SessionStartup.Wait(TimeSpan.FromSeconds(1800)))
            {
                Console.Error.WriteLine($"xeus-units: session startup timed out for {selected.Name}");
                return 1;
            }

            int GetBinNumber(dynamic u)
            {
                return binKind switch
                {
                    "functional" => u.ItuffUnit.Bin.FunctionalBin,
                    "full" => u.ItuffUnit.Bin.FullBin,
                    "hard" => u.ItuffUnit.Bin.HardBin,
                    _ => u.ItuffUnit.Bin.HardBin,
                };
            }

            var rows = session.Units
                .Select(u => new
                {
                    visualId = u.VisualId,
                    interfaceBin = u.ItuffUnit.Bin.HardBin,
                    functionalBin = u.ItuffUnit.Bin.FunctionalBin,
                    fullBin = u.ItuffUnit.Bin.FullBin,
                    selectedBin = GetBinNumber(u),
                    failTest = includeTest ? u.BinSetterTest?.Name : null,
                })
                .Where(r => !targetBin.HasValue || r.selectedBin == targetBin.Value)
                .OrderBy(r => r.visualId)
                .ToList();

            var topFailTests = rows
                .Where(r => !string.IsNullOrWhiteSpace(r.failTest))
                .GroupBy(r => r.failTest)
                .Select(g => new { test = g.Key, count = g.Count() })
                .OrderByDescending(x => x.count)
                .ThenBy(x => x.test)
                .ToList();

            Console.WriteLine(JsonSerializer.Serialize(new
            {
                selectedItuff = ItuffDefToDict(selected),
                query = new
                {
                    lot,
                    operation,
                    program,
                    wafer,
                    binKind,
                    targetBin,
                    includeTest,
                    matches = defs.Count,
                    backend = "XEUS"
                },
                totalUnits = session.Units.Count,
                filteredUnits = rows.Count,
                topFailTests,
                units = rows
            }, new JsonSerializerOptions { WriteIndented = true }));
        }

        return 0;
    }

    // -------------------------------------------------------------------------
    // Serialize an ItuffDefinition to a plain dictionary for JSON output
    // -------------------------------------------------------------------------
    static Dictionary<string, object?> ItuffDefToDict(ItuffDefinition d)
    {
        return new Dictionary<string, object?>
        {
            ["name"]             = d.Name,
            ["lot"]              = d.Lot,
            ["operation"]        = d.Operation,
            ["programName"]      = d.ProgramName,
            ["partType"]         = d.PartType,
            ["facility"]         = d.Facility,
            ["startDate"]        = d.StartDate?.ToString("o"),
            ["endDate"]          = d.EndDate?.ToString("o"),
            ["ituffDirectory"]   = d.ItuffDirectory,
            ["rootTpDirectory"]  = d.RootTpDirectory,
            ["stplDirectory"]    = d.StplDirectory,
            ["tplDirectory"]     = d.TplDirectory,
            ["totalLatestUnits"] = d.TotalLatestUnits,
            ["totalPassUnits"]   = d.TotalPassUnits,
            ["yield"]            = d.Yield,
            ["yieldText"]        = d.YieldText,
            ["sspec"]            = d.Sspec,
            ["engId"]            = d.EngId,
            ["temperature"]      = d.Temperature,
            ["materialType"]     = d.MaterialType.ToString(),
            ["isStaging"]        = d.IsStaging,
            ["errors"]           = d.Errors,
        };
    }

    static void PrintUsage()
    {
        Console.WriteLine(@"trace-bridge - TRACE API command-line bridge

Commands:
        search <query> [--type sort|class] [--site <site>] [--site-datasource CLASS|CLASSHDMT] [--sort-source AMR|GER|GAR]
      Search worldwide ituff index for a lot ID, visual ID, or program name.
      Default type is 'sort'.
            For class search, default site is FM.
            For class search, default site-datasource is CLASS.
            For sort search, default source is AMR.

  get-ituffs --lot <lot> [--operation <operation>]
      Fetch ituff definitions from Aries by lot (and optional operation).

  get-by-program --program <programName>
      Fetch ituff definitions from Aries by program name.

  get-by-visualid --visualid <visualId>
      Fetch ituff definitions from Aries by wafer scribe / visual ID.

    bin-dist --lot <lot> [--operation <op>] [--program <program>]
                     [--site <site>] [--site-datasource CLASS|CLASSHDMT]
                     [--bin-kind interface|hard|functional|full]
            Compute bin distribution from units of the latest matching ituff.
            Default site=JF, site-datasource=CLASSHDMT, bin-kind=interface(hard bin).

  xeus-get --lot <lot> [--operation <op>] [--program <program>] [--visualid <visualId>]
      Fetch ituff definitions from XEUS backend by lot.

  xeus-bin-dist --lot <lot> [--operation <op>] [--program <program>]
                           [--bin-kind interface|hard|functional|full]
            Compute bin distribution from units via XEUS backend.
            Default bin-kind=interface(hard bin).

    xeus-units --lot <lot> [--operation <op>] [--program <program>] [--wafer <wafer>]
                         [--bin-kind interface|hard|functional|full] [--bin <num>] [--include-test true|false]
                        Return per-unit rows (visualId, bins, fail test) from XEUS session.
                        Use --bin-kind interface --bin 8 to get interface-bin8 units.

Output is JSON written to stdout.
Errors are written to stderr (non-zero exit code) or as {""error"":..., ""message"":...} JSON.
");
    }
}
