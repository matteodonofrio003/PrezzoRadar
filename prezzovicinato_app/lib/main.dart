import 'package:flutter/material.dart';
import 'prezzovicinato_search_results.dart';

void main() {
  runApp(const PrezzoRadarApp());
}

class PrezzoRadarApp extends StatelessWidget {
  const PrezzoRadarApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'PrezzoRadar',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1A56DB)),
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _searchCtrl = TextEditingController();

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  // FIX: SearchResultsPage gestisce GPS internamente — qui non servono coordinate.
  // La funzione _getPosition() è stata rimossa da questo file.
  void _cerca() {
    final query = _searchCtrl.text.trim();
    if (query.isEmpty) return;

    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (context) => SearchResultsPage(
          initialQuery: query,
          // ✅ Rimosso userLat e userLon — non esistono più nel costruttore
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.radar, size: 80, color: Color(0xFF1A56DB)),
              const SizedBox(height: 16),
              const Text(
                'PrezzoRadar',
                style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              const Text(
                'Trova le offerte vicino a te',
                style: TextStyle(fontSize: 14, color: Color(0xFF6B7280)),
              ),
              const SizedBox(height: 40),
              TextField(
                controller:      _searchCtrl,
                textInputAction: TextInputAction.search,
                onSubmitted:     (_) => _cerca(),
                decoration: InputDecoration(
                  hintText:   'Cosa vuoi cercare? (es. Gin, Birra…)',
                  prefixIcon: const Icon(Icons.search),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(
                      color: Color(0xFF1A56DB),
                      width: 2,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width:  double.infinity,
                height: 50,
                child: FilledButton(
                  // FIX: rimosso _isLoading — il GPS ora parte dentro SearchResultsPage,
                  // quindi questa pagina non ha più bisogno di mostrare un loader.
                  onPressed: _cerca,
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF1A56DB),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                  child: const Text(
                    'Cerca Offerte Vicine',
                    style: TextStyle(fontSize: 16),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}