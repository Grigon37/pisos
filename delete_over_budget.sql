-- Elimina les 17 entrades que, un cop corregit el preu real (bug del
-- €/m2 confós amb el preu total), superen el sostre de cerca (391.000 €)
-- i per tant mai haurien d'haver aparegut a la llista.
DELETE FROM public.pisos
WHERE id IN (64, 65, 66, 68, 108, 156, 157, 158, 159, 160, 161, 228, 255, 278, 279, 332, 334);
