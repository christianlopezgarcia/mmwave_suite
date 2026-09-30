/*
* Copyright (C) 2024 Texas Instruments Incorporated
*
* All rights reserved not granted herein.
* Limited License.  
*
* Texas Instruments Incorporated grants a world-wide, royalty-free, 
* non-exclusive license under copyrights and patents it now or hereafter 
* owns or controls to make, have made, use, import, offer to sell and sell ("Utilize")
* this software subject to the terms herein.  With respect to the foregoing patent 
* license, such license is granted  solely to the extent that any such patent is necessary 
* to Utilize the software alone.  The patent license shall not apply to any combinations which 
* include this software, other than combinations with devices manufactured by or for TI ("TI Devices").  
* No hardware patent is licensed hereunder.
*
* Redistributions must preserve existing copyright notices and reproduce this license (including the 
* above copyright notice and the disclaimer and (if applicable) source code license limitations below) 
* in the documentation and/or other materials provided with the distribution
*
* Redistribution and use in binary form, without modification, are permitted provided that the following
* conditions are met:
*
*	* No reverse engineering, decompilation, or disassembly of this software is permitted with respect to any 
*     software provided in binary form.
*	* any redistribution and use are licensed by TI for use only with TI Devices.
*	* Nothing shall obligate TI to provide you with source code for the software licensed and provided to you in object code.
*
* If software source code is provided to you, modification and redistribution of the source code are permitted 
* provided that the following conditions are met:
*
*   * any redistribution and use of the source code, including any resulting derivative works, are licensed by 
*     TI for use only with TI Devices.
*   * any redistribution and use of any object code compiled from the source code and any resulting derivative 
*     works, are licensed by TI for use only with TI Devices.
*
* Neither the name of Texas Instruments Incorporated nor the names of its suppliers may be used to endorse or 
* promote products derived from this software without specific prior written permission.
*
* DISCLAIMER.
*
* THIS SOFTWARE IS PROVIDED BY TI AND TI'S LICENSORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, 
* BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. 
* IN NO EVENT SHALL TI AND TI'S LICENSORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR 
* CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, 
* OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, 
* OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE 
* POSSIBILITY OF SUCH DAMAGE.
*/

/*
 * gesture.c
 *
 */
/* Standard Include Files. */
#include <stdint.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

#include "gesture.h"

int8_t DOPPLER_WEIGHTS[128] = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    51,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
    59,
    60,
    61,
    62,
    63,
    -64,
    -63,
    -62,
    -61,
    -60,
    -59,
    -58,
    -57,
    -56,
    -55,
    -54,
    -53,
    -52,
    -51,
    -50,
    -49,
    -48,
    -47,
    -46,
    -45,
    -44,
    -43,
    -42,
    -41,
    -40,
    -39,
    -38,
    -37,
    -36,
    -35,
    -34,
    -33,
    -32,
    -31,
    -30,
    -29,
    -28,
    -27,
    -26,
    -25,
    -24,
    -23,
    -22,
    -21,
    -20,
    -19,
    -18,
    -17,
    -16,
    -15,
    -14,
    -13,
    -12,
    -11,
    -10,
    -9,
    -8,
    -7,
    -6,
    -5,
    -4,
    -3,
    -2,
    -1
};

extern Features_t   gfeatures;
extern ANN_struct_t gANN_struct_t;


/**
 *  @b Description
 *  @n
 *      Zero out the Doppler bins around zero in the detection matrix
 *
 *      These are the nearest 5 Doppler bins for both the positive and negative Doppler in addition to the Zero Doppler
 */
void Computefeatures_preStart(void)
{
    uint16_t  i, j;
    uint32_t *ptr;

    /*
     * In this target code, there are:
     * 64 range bins
     * 128 Doppler bins
     *
     * Doppler bins to zero out:
     * 1 to 5 (positive Doppler Bins)
     * 123 to 127 (negative Doppler Bins)
     * 0 (zero Doppler)
     */

    ptr = gfeatures.detMatrixData;
    for (i = 0; i < gfeatures.numRangeBins; i++)
    {
        for (j = 0; j < gfeatures.numDopplerBins; j++)
        {
            if (j <= DOPPLER_BINS_TO_SUPPRESS || j >= gfeatures.numDopplerBins - DOPPLER_BINS_TO_SUPPRESS)
            {
                ptr[j] = 0;
            }
        }

        ptr = ptr + gfeatures.numDopplerBins;
    }
}

/**
 *  @b Description
 *  @n
 *      Compute weighted range, weighted Doppler, and instantaneous energy
 */
void Computefeatures_RDIBased(void)
{
    uint32_t  wt, numDetections = 0;
    int16_t   rangeIdx, dopplerIdx;
    float     rangeAvg = 0, dopplerAvg = 0, wtSum = 0, wtSumPos = 0, wtSumNeg = 0, energy = 0, dopplerAvgPos = 0, dopplerAvgNeg = 0;
    uint32_t *tempPtr = NULL;

    /* In this demo, the positive Doppler bins are defined from Doppler Bin [1,N/2] and the negative Doppler bins are defined from Doppler Bin [N/2,N] */
    // set the temporary pointer to Range Bin 1 Doppler Bin 0 and increment based on the order of the Detection Matrix
    tempPtr = gfeatures.detMatrixData + gfeatures.numDopplerBins;

    // Compute the sum of the detection matrix for the range bins of interest [1 7] along with the weighted range average
    for (rangeIdx = RANGE_BIN_START; rangeIdx < RANGE_BIN_END; rangeIdx++)
    {
        for (dopplerIdx = 0; dopplerIdx < gfeatures.numDopplerBins; dopplerIdx++)
        {
            // Obtain the weight from the current detection matrix value
            wt = *tempPtr;
            energy += (float)wt;

            // Increment the number of detections for values larger than the threshold
            if (wt > THRESH_NUM_POINTS)
            {
                numDetections++;
            }

            // Add the current weight to the overall weight sum
            wtSum += (float)wt;

            // Calculate the weighted range for the current value and add it to the overall weighted range
            rangeAvg += wt * ((float)rangeIdx); // float promotion

            // Calculate the weighted doppler for the current value and add it to the overall weighted doppler
            dopplerAvg += wt * ((float)DOPPLER_WEIGHTS[dopplerIdx]); // float promotion

            // Calculate the weighted doppler for positive and negative doppler bins
            if (dopplerIdx < gfeatures.numDopplerBins / 2)
            {
                wtSumPos += (float)wt;
                dopplerAvgPos += wt * ((float)DOPPLER_WEIGHTS[dopplerIdx]);
            }
            else
            {
                wtSumNeg += (float)wt;
                dopplerAvgNeg += wt * ((float)DOPPLER_WEIGHTS[dopplerIdx]);
            }

            // Increment the pointer to the next value in the detection matrix
            tempPtr++;
        }
    }

    if (wtSum > 0)
    {
        gfeatures.pWtrange   = rangeAvg / wtSum;
        gfeatures.pWtdoppler = dopplerAvg / wtSum;
    }

    if (wtSumPos > 0)
    {
        gfeatures.pWtdopplerPos = dopplerAvgPos / wtSumPos;
    }

    if (wtSumNeg > 0)
    {
        gfeatures.pWtdopplerNeg = dopplerAvgNeg / wtSumNeg;
    }

    gfeatures.pInstenergy    = energy / 10000;
    gfeatures.pNumDetections = (float)numDetections;
}

/**
 *  @b Description
 *  @n
 *      Create an array for the 2D Input to the angle fft
 */
void Computefeatures_DOABased(void)
{
    uint32_t      *ptr;
    cmplx32ImRe_t *DOA_Input_ptr;
    int16_t        rangeIdx, dopplerIdx;
    cmplx32ImRe_t  DOA_Input[gfeatures.numVirtualAntennas * 2]; // Need to account for the fact that there are 4 physical RX channels

    /* Now find the largest value in the detection matrix in the first RANGE_BIN_END of range bins according to the MATLAB model
     *
     * In this target code, there are:
     * 64 range bins
     * 128 Doppler bins
     *
     * Search through the first RANGE_BIN_END range bins and across all Doppler bins
     */
    gfeatures.rangeIndex    = 0;
    gfeatures.dopplerIndex  = 0;
    ptr                     = gfeatures.detMatrixData;
    gfeatures.max_value_ptr = ptr;
    for (rangeIdx = 0; rangeIdx < RANGE_BIN_END; rangeIdx++)
    {
        for (dopplerIdx = 0; dopplerIdx < gfeatures.numDopplerBins; dopplerIdx++)
        {
            if (*ptr > *gfeatures.max_value_ptr)
            {
                gfeatures.max_value_ptr = ptr;
                gfeatures.rangeIndex    = rangeIdx;
                gfeatures.dopplerIndex  = dopplerIdx;
            }
            ptr++;
        }
    }

    gfeatures.currentWeight = (float)*gfeatures.max_value_ptr;

    /* Use the range index and doppler index values to navigate to the location of 2D FFT Output samples */
    DOA_Input_ptr = gfeatures.dopplerCubeData;
    DOA_Input_ptr += (gfeatures.rangeIndex * gfeatures.numDopplerBins * gfeatures.numVirtualAntennas) +
        (gfeatures.dopplerIndex * gfeatures.numVirtualAntennas);

    /* Create an array for the 2D Input
     * Since there are 4 physical RX channels on this device, this needs to be accounted for in the virtual samples array
     * RX1 and RX4 values need to be multiplied by -1 to account for 180 degree phase difference between RX1 and RX2
     * Also need to take into consideration the fact that the RX1 virtual antennas are separated by a factor of lamba and not lambda/2
     *
     * 2D FFT Data is organized in the following manner:
     * [    0           1           2           3  ]
     * [TX1-RX1     TX1-RX2     TX1-RX3     TX1-RX4]
     *
     * Input data for the angle calculation will be organized in the following manner:
     * [0           1           2            3             4           5           6       7]
     * [TX1-RX1     TX1-RX2     TX1-RX4      TX1-RX3       0           0           0       0]
     *
     * This simplifies the HWA paramset set implementation since HWA can only zero pad and not zero fill
     *
     */

#if defined(MMW_6843_ODS)
    /* Populate DOA_Input with the four input samples from the 2D FFT Output */
    // TX1-RX1
    DOA_Input[0] = *DOA_Input_ptr;
    DOA_Input_ptr++;
    // TX1-RX2
    DOA_Input[1] = *DOA_Input_ptr;
    DOA_Input_ptr++;
    // TX1-RX3
    DOA_Input[3] = *DOA_Input_ptr;
    DOA_Input_ptr++;
    // TX1-RX4
    DOA_Input[2] = *DOA_Input_ptr;

    /* Apply the Phase Correction coefficient to the RX1 and RX4 values */
    /* The 6843 ODS Antenna has RX1 and RX4 fed from the opposite side so all
     * corresponding virtual RXs channels need to be inverted by 180 degrees
     */
    // TX1-RX1
    DOA_Input[0].imag *= PHASE_CORRECTION;
    DOA_Input[0].real *= PHASE_CORRECTION;
    // TX1-RX4
    DOA_Input[2].imag *= PHASE_CORRECTION;
    DOA_Input[2].real *= PHASE_CORRECTION;

    /* Scale the 2D FFT input values down by 2^6 (64) */
    // TX1-RX1
    DOA_Input[0].imag = DOA_Input[0].imag / 64;
    DOA_Input[0].real = DOA_Input[0].real / 64;
    // TX1-RX2
    DOA_Input[1].imag = DOA_Input[1].imag / 64;
    DOA_Input[1].real = DOA_Input[1].real / 64;
    // TX1-RX3
    DOA_Input[3].imag = DOA_Input[3].imag / 64;
    DOA_Input[3].real = DOA_Input[3].real / 64;
    // TX1-RX4
    DOA_Input[2].imag = DOA_Input[2].imag / 64;
    DOA_Input[2].real = DOA_Input[2].real / 64;

    /* Fill in the remaining values */
    // TX2-RX1
    DOA_Input[4].imag = 0;
    DOA_Input[4].real = 0;
    // TX2-RX2
    DOA_Input[5].imag = 0;
    DOA_Input[5].real = 0;
    // TX2-RX3
    DOA_Input[7].imag = 0;
    DOA_Input[7].real = 0;
    // TX2-RX4
    DOA_Input[6].imag = 0;
    DOA_Input[6].real = 0;
#endif

#if defined(MMW_6843_AOP)
    /* Populate DOA_Input with the four input samples from the 2D FFT Output */
    // TX1-RX1
    DOA_Input[3] = *DOA_Input_ptr;
    DOA_Input_ptr++;
    // TX1-RX2
    DOA_Input[2] = *DOA_Input_ptr;
    DOA_Input_ptr++;
    // TX1-RX3
    DOA_Input[1] = *DOA_Input_ptr;
    DOA_Input_ptr++;
    // TX1-RX4
    DOA_Input[0] = *DOA_Input_ptr;

    /* Apply the Phase Correction coefficient to the RX2 and RX4 values */
    /* The 6843 AOP Antenna has RX1 and RX3 fed from the opposite side so all
     * corresponding virtual RXs channels need to be inverted by 180 degrees
     */
    // TX1-RX1
    DOA_Input[0].imag *= PHASE_CORRECTION;
    DOA_Input[0].real *= PHASE_CORRECTION;
    // TX1-RX3
    DOA_Input[2].imag *= PHASE_CORRECTION;
    DOA_Input[2].real *= PHASE_CORRECTION;

    /* Scale the 2D FFT input values down by 2^6 (64) */
    // TX1-RX1
    DOA_Input[3].imag = DOA_Input[3].imag / 64;
    DOA_Input[3].real = DOA_Input[3].real / 64;
    // TX1-RX2
    DOA_Input[2].imag = DOA_Input[2].imag / 64;
    DOA_Input[2].real = DOA_Input[2].real / 64;
    // TX1-RX3
    DOA_Input[1].imag = DOA_Input[1].imag / 64;
    DOA_Input[1].real = DOA_Input[1].real / 64;
    // TX1-RX4
    DOA_Input[0].imag = DOA_Input[0].imag / 64;
    DOA_Input[0].real = DOA_Input[0].real / 64;

    /* Fill in the remaining values */
    // TX2-RX1
    DOA_Input[4].imag = 0;
    DOA_Input[4].real = 0;
    // TX2-RX2
    DOA_Input[5].imag = 0;
    DOA_Input[5].real = 0;
    // TX2-RX3
    DOA_Input[7].imag = 0;
    DOA_Input[7].real = 0;
    // TX2-RX4
    DOA_Input[6].imag = 0;
    DOA_Input[6].real = 0;
#endif

    /* Transfer DOA_Input samples to HWA Memory Bank M0 and prepare to execute HWA paramsets*/
    memcpy((void *)gfeatures.HWA_base_address, (void *)&DOA_Input, sizeof(DOA_Input));

    /* Zero out the selected index in the detection matrix for the next loop */
    // gfeatures.detMatrixData[max_value_index] = 0;
    *gfeatures.max_value_ptr = 0;
}

/**
 *  @b Description
 *  @n
 *      Find the azimuth and elevation index for the largest point in the angle fft output
 */
void ComputeAngleStats(void)
{
    uint16_t  i;
    uint32_t *DOA_Output_ptr;

    /* Set the DOA_Output_ptr to the output of the angle fft */
    DOA_Output_ptr = (void *)gfeatures.HWA_base_address;

    /* Now find the largest value in the angle matrix
     *
     * In this target code, there are:
     * 32 azimuth bins
     * 32 elevation bins
     *
     */
    gfeatures.max_value_ptr = DOA_Output_ptr;
    for (i = 0; i < NUM_ANGLE_BINS_FEATURES * NUM_ANGLE_BINS_FEATURES; i++)
    {
        if (*DOA_Output_ptr > *gfeatures.max_value_ptr)
        {
            gfeatures.max_value_ptr  = DOA_Output_ptr;
            gfeatures.azimuthIndex   = i / NUM_ANGLE_BINS_FEATURES;
            gfeatures.elevationIndex = i - (gfeatures.azimuthIndex * NUM_ANGLE_BINS_FEATURES);
        }
        DOA_Output_ptr++;
    }

    if (gfeatures.azimuthIndex > (NUM_ANGLE_BINS_FEATURES / 2) - 1)
    {
        gfeatures.azimuthIndex = gfeatures.azimuthIndex - NUM_ANGLE_BINS_FEATURES;
    }

    if (gfeatures.elevationIndex > (NUM_ANGLE_BINS_FEATURES / 2) - 1)
    {
        gfeatures.elevationIndex = gfeatures.elevationIndex - NUM_ANGLE_BINS_FEATURES;
    }
}

/**
 *  @b Description
 *  @n
 *      Compute correlation coefficient b/w azimuth angle and doppler by looking at last 20 frames including the current one
 */
void Computefeatures_Hybrid()
{
    int32_t ii;
    float   azmean = 0.0, doppmean = 0.0, sigma_az = 0.0, sigma_dopp = 0.0, corr_coeff, crosscorr = 0.0, eps = 1e-16;

    // compute the mean
    for (ii = 0; ii < LEN_CORR; ii++)
    {
        azmean += gfeatures.az_corr_buf[ii];
        doppmean += gfeatures.dopp_corr_buf[ii];
    }
    azmean   = azmean / LEN_CORR;
    doppmean = doppmean / LEN_CORR;

    // compute sigma_az, sigma_dopp, crosscorr
    for (ii = 0; ii < LEN_CORR; ii++)
    {
        crosscorr += (gfeatures.az_corr_buf[ii] - azmean) * (gfeatures.dopp_corr_buf[ii] - doppmean);
        sigma_az += (gfeatures.az_corr_buf[ii] - azmean) * (gfeatures.az_corr_buf[ii] - azmean);
        sigma_dopp += (gfeatures.dopp_corr_buf[ii] - doppmean) * (gfeatures.dopp_corr_buf[ii] - doppmean);
    }
    // not dividing by N, optimizing calculation, adding eps to avoid corner case
    sigma_az = sqrt(sigma_az) + eps;
    // not dividing by N, optimizing calculation, adding eps to avoid corner case
    sigma_dopp = sqrt(sigma_dopp) + eps;
    corr_coeff = crosscorr / (sigma_az * sigma_dopp);

    gfeatures.pAzdoppcorr = corr_coeff;
}

/**
 *  @b Description
 *  @n
 *      Post processing on the ann output probabilities to determine if a gesture has occurred.
 */
void FindGesture()
{
    uint32_t i, j, confSum = 0;

    // Shift the existing values
    for (i = 0; i < GESTURE_FEATURE_LENGTH * NUM_OUTPUT_PROBABILITIES - NUM_OUTPUT_PROBABILITIES; i++)
    {
        gfeatures.sumProbs[i] = gfeatures.sumProbs[i + NUM_OUTPUT_PROBABILITIES];
    }

    // Add the values for the current frame
    for (i = 0; i < NUM_OUTPUT_PROBABILITIES; i++)
    {
        if (gANN_struct_t.prob[i] >= GESTURE_PROBABILITY_THRESHOLDS[i])
        {
            gfeatures.sumProbs[GESTURE_FEATURE_LENGTH * NUM_OUTPUT_PROBABILITIES - NUM_OUTPUT_PROBABILITIES + i] = 1;
        }
        else
        {
            gfeatures.sumProbs[GESTURE_FEATURE_LENGTH * NUM_OUTPUT_PROBABILITIES - NUM_OUTPUT_PROBABILITIES + i] = 0;
        }
    }

    gfeatures.currFramegesture = 0;

    for (i = 0; i < NUM_OUTPUT_PROBABILITIES; i++)
    {
        confSum = 0;

        for (j = 0; j < GESTURE_FEATURE_LENGTH; j++)
        {
            confSum += gfeatures.sumProbs[j * NUM_OUTPUT_PROBABILITIES + i];
        }

        // Sum must be larger than count threshold to be considered a gesture
        if (confSum > GESTURE_COUNT_THRESHOLDS[i])
        {
            gfeatures.currFramegesture = i;
        }
    }

    if (gfeatures.prevFramegesture != gfeatures.currFramegesture)
    {
        if (gfeatures.currFramegesture != IDX_NO_GESTURE)
        {
            CLI_write(GEST_OUTPUTS[gfeatures.currFramegesture]);
        }
    }
    else
    {
        if (gfeatures.frameCount % 15 == 0)
        {
            if (gfeatures.currFramegesture == IDX_CW_TWIRL_GESTURE ||
                gfeatures.currFramegesture == IDX_CCW_TWIRL_GESTURE ||
                gfeatures.currFramegesture == IDX_SHINE_GESTURE ||
                gfeatures.currFramegesture == IDX_NO_GESTURE)
            {
                CLI_write(GEST_OUTPUTS[gfeatures.currFramegesture]);
            }
        }
    }

    gfeatures.prevFramegesture = gfeatures.currFramegesture;
}
