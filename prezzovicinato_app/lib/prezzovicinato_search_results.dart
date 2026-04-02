// =============================================================================
//  PrezzoVicinato — Search Results Page  (versione MVP corretta)
//
//  pubspec.yaml dependencies:
//    cached_network_image: ^3.3.1
//    shimmer: ^3.0.0
//    geolocator: ^11.0.0
//    http: ^1.2.0
// =============================================================================

import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:shimmer/shimmer.dart';
import 'package:http/http.dart' as http;
import 'package:geolocator/geolocator.dart';

// ── Modello dati ──────────────────────────────────────────────────────────────

class OfferResult {
  final String supermercatoId;
  final String catena;
  final String nomePuntoVendita;
  final String indirizzo;
  final String? logoUrl;
  final String nomeProdotto;
  final String? marca;
  final String? quantita;
  final double prezzo;
  final double? prezzoOriginale;
  final double distanzaKm;
  final DateTime dataFine;

  const OfferResult({
    required this.supermercatoId,
    required this.catena,
    required this.nomePuntoVendita,
    required this.indirizzo,
    this.logoUrl,
    required this.nomeProdotto,
    this.marca,
    this.quantita,
    required this.prezzo,
    this.prezzoOriginale,
    required this.distanzaKm,
    required this.dataFine,
  });

  factory OfferResult.fromJson(Map<String, dynamic> j) => OfferResult(
        supermercatoId:   j['supermercato_id'] as String,
        catena:           j['catena'] as String,
        nomePuntoVendita: (j['nome_punto_vendita'] ?? j['catena']) as String,
        indirizzo:        (j['indirizzo'] ?? '') as String,
        logoUrl:          j['logo_url'] as String?,
        nomeProdotto:     j['nome_prodotto'] as String,
        marca:            j['marca'] as String?,
        quantita:         j['quantita'] as String?,
        prezzo:           (j['prezzo'] as num).toDouble(),
        prezzoOriginale:  (j['prezzo_originale'] as num?)?.toDouble(),
        distanzaKm:       (j['distanza_km'] as num).toDouble(),
        dataFine:         DateTime.parse(j['data_fine'] as String),
      );

  int? get scontoPercent {
    if (prezzoOriginale == null || prezzoOriginale! <= prezzo) return null;
    return (((prezzoOriginale! - prezzo) / prezzoOriginale!) * 100).round();
  }

  String get distanzaLabel => distanzaKm < 1
      ? '${(distanzaKm * 1000).round()} m'
      : '${distanzaKm.toStringAsFixed(1)} km';
}

// ── API Service ───────────────────────────────────────────────────────────────

class PrezzoVicinatoApi {
  // ⚠️  Cambia con il tuo IP (ipconfig → IPv4)
  // Emulatore Android → http://10.0.2.2:8000
  // Telefono fisico   → http://IP_DEL_PC:8000
  static const _baseUrl = 'http://192.168.1.59:8000';

  static Future<List<OfferResult>> search({
    required String query,
    required double lat,
    required double lon,
    int raggioM = 500000,
  }) async {
    final uri = Uri.parse('$_baseUrl/search').replace(queryParameters: {
      'q':      query,
      'lat':    lat.toString(),
      'lon':    lon.toString(),
      'raggio': raggioM.toString(),
    });

    final resp = await http.get(uri).timeout(const Duration(seconds: 10));
    if (resp.statusCode != 200) {
      throw Exception('Errore API: ${resp.statusCode}');
    }

    final body = json.decode(resp.body) as Map<String, dynamic>;
    final list = body['results'] as List<dynamic>;
    return list
        .map((e) => OfferResult.fromJson(e as Map<String, dynamic>))
        .toList();
  }
}

// ── Helper GPS con fallback automatico ───────────────────────────────────────

// Coordinate di fallback = stesse del seed_db.py
const double _fallbackLat = 40.8518;
const double _fallbackLon = 14.2681;

Future<({double lat, double lon})> getPosition() async {
  try {
    final serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      debugPrint('GPS disattivato → uso coordinate di test');
      return (lat: _fallbackLat, lon: _fallbackLon);
    }

    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      debugPrint('Permesso GPS negato → uso coordinate di test');
      return (lat: _fallbackLat, lon: _fallbackLon);
    }

    final pos = await Geolocator.getCurrentPosition(
      desiredAccuracy: LocationAccuracy.low,
      timeLimit: const Duration(seconds: 6),
    );
    return (lat: pos.latitude, lon: pos.longitude);
  } catch (e) {
    debugPrint('Errore GPS: $e → uso coordinate di test');
    return (lat: _fallbackLat, lon: _fallbackLon);
  }
}

// ── Schermata principale ──────────────────────────────────────────────────────

class SearchResultsPage extends StatefulWidget {
  final String initialQuery;

  // FIX: lat/lon non passati dall'esterno — la pagina li ottiene da sola
  const SearchResultsPage({
    super.key,
    this.initialQuery = '',
  });

  @override
  State<SearchResultsPage> createState() => _SearchResultsPageState();
}

class _SearchResultsPageState extends State<SearchResultsPage> {
  late final TextEditingController _searchCtrl;

  // Parte con lista vuota — nessun errore, nessun caricamento a vuoto
  Future<List<OfferResult>> _future = Future.value([]);

  @override
  void initState() {
    super.initState();
    _searchCtrl = TextEditingController(text: widget.initialQuery);
    if (widget.initialQuery.isNotEmpty) {
      _doSearch(widget.initialQuery);
    }
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  // Funzione async separata — risolve il problema VoidCallback vs Future<void>
  Future<void> _doSearch(String query) async {
    final q = query.trim();
    if (q.isEmpty) return;

    setState(() {
      _future = Future(() async {
        final pos = await getPosition();
        return PrezzoVicinatoApi.search(
          query:  q,
          lat:    pos.lat,
          lon:    pos.lon,
        );
      });
    });
  }

  // VoidCallback sincrono che chiama la funzione async
  void _onSearchPressed() => _doSearch(_searchCtrl.text);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFF4F6F9),
      appBar: AppBar(
        elevation:       0,
        backgroundColor: const Color(0xFF1A56DB),
        title: const Row(
          children: [
            Icon(Icons.local_offer_rounded, color: Colors.white, size: 22),
            SizedBox(width: 8),
            Text(
              'PrezzoVicinato',
              style: TextStyle(
                color:        Colors.white,
                fontWeight:   FontWeight.w700,
                fontSize:     18,
                letterSpacing: -0.3,
              ),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon:     const Icon(Icons.map_outlined, color: Colors.white),
            tooltip:  'Mappa',
            onPressed: () {},
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(
        children: [
          _SearchBar(controller: _searchCtrl, onSearch: _onSearchPressed),
          Expanded(child: _ResultsBody(future: _future)),
        ],
      ),
    );
  }
}

// ── Search bar ────────────────────────────────────────────────────────────────

class _SearchBar extends StatelessWidget {
  final TextEditingController controller;
  final VoidCallback onSearch;

  const _SearchBar({required this.controller, required this.onSearch});

  @override
  Widget build(BuildContext context) => Container(
        color:   const Color(0xFF1A56DB),
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller:      controller,
                onSubmitted:     (_) => onSearch(),
                textInputAction: TextInputAction.search,
                style:           const TextStyle(fontSize: 15),
                decoration: InputDecoration(
                  hintText:  "Cerca prodotto… (es: Gin Gordon's)",
                  filled:    true,
                  fillColor: Colors.white,
                  prefixIcon: const Icon(Icons.search, color: Color(0xFF9CA3AF)),
                  suffixIcon: controller.text.isNotEmpty
                      ? IconButton(
                          icon:      const Icon(Icons.close, size: 18),
                          onPressed: controller.clear,
                        )
                      : null,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide:   BorderSide.none,
                  ),
                  contentPadding: const EdgeInsets.symmetric(vertical: 12),
                ),
              ),
            ),
            const SizedBox(width: 10),
            FilledButton(
              onPressed: onSearch,
              style: FilledButton.styleFrom(
                backgroundColor: const Color(0xFFFBBF24),
                foregroundColor: const Color(0xFF1F2937),
                minimumSize:     const Size(56, 48),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
              child: const Icon(Icons.search),
            ),
          ],
        ),
      );
}

// ── Corpo risultati ───────────────────────────────────────────────────────────

class _ResultsBody extends StatelessWidget {
  final Future<List<OfferResult>> future;
  const _ResultsBody({required this.future});

  @override
  Widget build(BuildContext context) => FutureBuilder<List<OfferResult>>(
        future: future,
        builder: (context, snap) {
          if (snap.connectionState == ConnectionState.waiting) {
            return _SkeletonList();
          }
          if (snap.hasError) {
            return _ErrorState(message: snap.error.toString());
          }
          final results = snap.data ?? [];
          if (results.isEmpty) return const _EmptyState();
          return _ResultList(results: results);
        },
      );
}

class _ResultList extends StatelessWidget {
  final List<OfferResult> results;
  const _ResultList({required this.results});

  @override
  Widget build(BuildContext context) => ListView.builder(
        padding:   const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        itemCount: results.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) return _ResultsHeader(count: results.length);
          return _OfferCard(offer: results[index - 1]);
        },
      );
}

class _ResultsHeader extends StatelessWidget {
  final int count;
  const _ResultsHeader({required this.count});

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8, left: 4),
        child: Text(
          '$count ${count == 1 ? 'risultato' : 'risultati'} nelle vicinanze',
          style: Theme.of(context)
              .textTheme
              .bodySmall
              ?.copyWith(color: const Color(0xFF6B7280)),
        ),
      );
}

// ── Card offerta ──────────────────────────────────────────────────────────────

class _OfferCard extends StatelessWidget {
  final OfferResult offer;
  const _OfferCard({required this.offer});

  @override
  Widget build(BuildContext context) {
    final sconto = offer.scontoPercent;
    final gg     = offer.dataFine.difference(DateTime.now()).inDays;

    return Card(
      margin:       const EdgeInsets.only(bottom: 10),
      elevation:    0,
      color:        Colors.white,
      shape:        RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () {},
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _SupermarketLogo(url: offer.logoUrl, catena: offer.catena),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Expanded(
                          child: Text(
                            offer.nomePuntoVendita,
                            style: const TextStyle(
                              fontSize:   12,
                              fontWeight: FontWeight.w600,
                              color:      Color(0xFF1A56DB),
                            ),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        _DistanceBadge(label: offer.distanzaLabel),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      offer.marca != null
                          ? '${offer.marca} — ${offer.nomeProdotto}'
                          : offer.nomeProdotto,
                      style: const TextStyle(
                        fontSize:   15,
                        fontWeight: FontWeight.w600,
                        color:      Color(0xFF111827),
                        height:     1.3,
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 2),
                    if (offer.quantita != null)
                      Text(
                        offer.quantita!,
                        style: const TextStyle(
                            fontSize: 12, color: Color(0xFF9CA3AF)),
                      ),
                    const SizedBox(height: 8),
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text(
                          '€ ${offer.prezzo.toStringAsFixed(2)}',
                          style: const TextStyle(
                            fontSize:   22,
                            fontWeight: FontWeight.w700,
                            color:      Color(0xFF059669),
                          ),
                        ),
                        const SizedBox(width: 8),
                        if (offer.prezzoOriginale != null)
                          Text(
                            '€ ${offer.prezzoOriginale!.toStringAsFixed(2)}',
                            style: const TextStyle(
                              fontSize:   13,
                              color:      Color(0xFF9CA3AF),
                              decoration: TextDecoration.lineThrough,
                            ),
                          ),
                        const Spacer(),
                        if (sconto != null) _ScontoBadge(percent: sconto),
                      ],
                    ),
                    const SizedBox(height: 6),
                    _ScadenzaLabel(giorniRimasti: gg),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Sub-widget ────────────────────────────────────────────────────────────────

class _SupermarketLogo extends StatelessWidget {
  final String? url;
  final String  catena;
  const _SupermarketLogo({required this.url, required this.catena});

  @override
  Widget build(BuildContext context) => ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: SizedBox(
          width:  56,
          height: 56,
          child: url != null
              ? CachedNetworkImage(
                  imageUrl:    url!,
                  fit:         BoxFit.contain,
                  // FIX: (_, __) e (_, __, ___) — Dart 3 non ammette _ duplicato
                  placeholder:  (_, __) => _LogoFallback(catena: catena),
                  errorWidget:  (_, __, ___) => _LogoFallback(catena: catena),
                )
              : _LogoFallback(catena: catena),
        ),
      );
}

class _LogoFallback extends StatelessWidget {
  final String catena;
  const _LogoFallback({required this.catena});

  @override
  Widget build(BuildContext context) => Container(
        color:     const Color(0xFFF3F4F6),
        alignment: Alignment.center,
        child: Text(
          catena.substring(0, catena.length.clamp(0, 2)).toUpperCase(),
          style: const TextStyle(
            fontSize:   18,
            fontWeight: FontWeight.w700,
            color:      Color(0xFF6B7280),
          ),
        ),
      );
}

class _DistanceBadge extends StatelessWidget {
  final String label;
  const _DistanceBadge({required this.label});

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color:        const Color(0xFFEFF6FF),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.place_outlined, size: 12, color: Color(0xFF1A56DB)),
            const SizedBox(width: 2),
            Text(
              label,
              style: const TextStyle(
                fontSize:   11,
                fontWeight: FontWeight.w600,
                color:      Color(0xFF1A56DB),
              ),
            ),
          ],
        ),
      );
}

class _ScontoBadge extends StatelessWidget {
  final int percent;
  const _ScontoBadge({required this.percent});

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color:        const Color(0xFFFEF3C7),
          borderRadius: BorderRadius.circular(6),
        ),
        child: Text(
          '-$percent%',
          style: const TextStyle(
            fontSize:   12,
            fontWeight: FontWeight.w700,
            color:      Color(0xFF92400E),
          ),
        ),
      );
}

class _ScadenzaLabel extends StatelessWidget {
  final int giorniRimasti;
  const _ScadenzaLabel({required this.giorniRimasti});

  @override
  Widget build(BuildContext context) {
    final isUrgente = giorniRimasti <= 2;
    return Row(
      children: [
        Icon(
          Icons.schedule_rounded,
          size:  12,
          color: isUrgente ? const Color(0xFFDC2626) : const Color(0xFF9CA3AF),
        ),
        const SizedBox(width: 4),
        Text(
          giorniRimasti <= 0
              ? 'Scade oggi'
              : 'Ancora $giorniRimasti ${giorniRimasti == 1 ? 'giorno' : 'giorni'}',
          style: TextStyle(
            fontSize:   11,
            color:      isUrgente ? const Color(0xFFDC2626) : const Color(0xFF9CA3AF),
            fontWeight: isUrgente ? FontWeight.w600 : FontWeight.normal,
          ),
        ),
      ],
    );
  }
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

class _SkeletonList extends StatelessWidget {
  @override
  Widget build(BuildContext context) => Shimmer.fromColors(
        baseColor:      const Color(0xFFE5E7EB),
        highlightColor: const Color(0xFFF9FAFB),
        child: ListView.builder(
          padding:   const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          itemCount: 5,
          // FIX: (_, __) — underscore duplicato non compila in Dart 3
          itemBuilder: (_, __) => const _SkeletonCard(),
        ),
      );
}

class _SkeletonCard extends StatelessWidget {
  const _SkeletonCard();

  @override
  Widget build(BuildContext context) => Card(
        margin:    const EdgeInsets.only(bottom: 10),
        elevation: 0,
        color:     Colors.white,
        shape:     RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              Container(
                width:  56,
                height: 56,
                decoration: BoxDecoration(
                  color:        const Color(0xFFE5E7EB),
                  borderRadius: BorderRadius.circular(10),
                ),
              ),
              const SizedBox(width: 14),
              const Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _SkeletonBox(width: 120,            height: 12),
                    SizedBox(height: 8),
                    _SkeletonBox(width: double.infinity, height: 14),
                    SizedBox(height: 4),
                    _SkeletonBox(width: 80,             height: 12),
                    SizedBox(height: 10),
                    _SkeletonBox(width: 70,             height: 22),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
}

class _SkeletonBox extends StatelessWidget {
  final double width;
  final double height;
  const _SkeletonBox({required this.width, required this.height});

  @override
  Widget build(BuildContext context) => Container(
        width:  width,
        height: height,
        decoration: BoxDecoration(
          color:        const Color(0xFFE5E7EB),
          borderRadius: BorderRadius.circular(4),
        ),
      );
}

// ── Empty & Error state ───────────────────────────────────────────────────────

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) => const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.search_off_rounded, size: 64, color: Color(0xFFD1D5DB)),
              SizedBox(height: 16),
              Text(
                'Nessuna offerta trovata nelle vicinanze',
                style:     TextStyle(fontSize: 16, color: Color(0xFF6B7280)),
                textAlign: TextAlign.center,
              ),
              SizedBox(height: 8),
              Text(
                'Prova ad allargare il raggio di ricerca\no usa un termine diverso.',
                style:     TextStyle(fontSize: 13, color: Color(0xFF9CA3AF)),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
}

class _ErrorState extends StatelessWidget {
  final String message;
  const _ErrorState({required this.message});

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.wifi_off_rounded, size: 52, color: Color(0xFFD1D5DB)),
              const SizedBox(height: 16),
              const Text(
                'Impossibile caricare i risultati',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 8),
              Text(
                message,
                style:     const TextStyle(fontSize: 12, color: Color(0xFF9CA3AF)),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
}