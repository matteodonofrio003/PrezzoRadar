import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'prezzovicinato_search_results.dart'; // Richiama la schermata creata l'altra volta

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
  bool _isLoading = false;

  // 📍 ECCO LA FUNZIONE DI CLAUDE INSERITA QUI
  Future<Position> _getPosition() async {
    bool enabled = await Geolocator.isLocationServiceEnabled();
    if (!enabled) throw Exception('GPS disattivato');

    LocationPermission perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
      if (perm == LocationPermission.denied) throw Exception('Permesso negato');
    }
    if (perm == LocationPermission.deniedForever) {
      throw Exception('Permessi negati permanentemente. Vai nelle impostazioni.');
    }
    return await Geolocator.getCurrentPosition();
  }

  // 🚀 FUNZIONE CHE PARTE AL CLICK DEL BOTTONE
  void _cerca() async {
    if (_searchCtrl.text.trim().isEmpty) return;

    setState(() => _isLoading = true);

    try {
      // 1. Chiede il permesso e calcola il GPS
      final pos = await _getPosition();

      // 2. Apre la schermata dei risultati passandogli le coordinate!
      if (mounted) {
        Navigator.push(
          context,
          MaterialPageRoute(
            builder: (context) => SearchResultsPage(
              initialQuery: _searchCtrl.text.trim(),
              userLat: pos.latitude,
              userLon: pos.longitude,
            ),
          ),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Errore GPS: $e')),
      );
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
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
              const SizedBox(height: 40),
              TextField(
                controller: _searchCtrl,
                decoration: InputDecoration(
                  hintText: 'Cosa vuoi cercare? (es. Gin)',
                  prefixIcon: const Icon(Icons.search),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                onSubmitted: (_) => _cerca(),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                height: 50,
                child: FilledButton(
                  onPressed: _isLoading ? null : _cerca,
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF1A56DB),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                  child: _isLoading
                      ? const SizedBox(
                          width: 24,
                          height: 24,
                          child: CircularProgressIndicator(color: Colors.white, strokeWidth: 2),
                        )
                      : const Text('Cerca Offerte Vicine', style: TextStyle(fontSize: 16)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}